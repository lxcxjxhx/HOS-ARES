"""RouteSet 缓存与 single-flight（27 §3.7）。

- 内容寻址：``routeset_sha256`` 即缓存身份。
- TTL：过期**不删**，只标 stale（审计要能看旧解析）。
- 失败缓存：``ROUTE_UNRESOLVED`` 结果只缓存 60s，防止对目标连打。
- single-flight：同键并发只有一个真跑。
"""

from __future__ import annotations

import threading
from typing import Callable, TypeVar

from .types import RouteSet

T = TypeVar("T")
SHORT_TTL_S = 60


def cache_key(asset_id: str, query_hash: str, profile_hash: str, resolver_version: str) -> str:
    return f"{asset_id}|{query_hash}|{profile_hash}|{resolver_version}"


class RouteSetCache:
    """进程内缓存：{key -> RouteSet}，另按 sha 索引（内容寻址）。"""

    def __init__(self) -> None:
        self._by_key: dict[str, RouteSet] = {}
        self._short: dict[str, int] = {}  # key -> 失败缓存的 expiries_at

    def get(self, key: str) -> RouteSet | None:
        return self._by_key.get(key)

    def put(self, key: str, rs: RouteSet) -> None:
        self._by_key[key] = rs
        self._short.pop(key, None)

    def put_short(self, key: str, rs: RouteSet, now: int) -> None:
        self._by_key[key] = rs
        self._short[key] = now + SHORT_TTL_S

    def is_short(self, key: str) -> bool:
        return key in self._short

    def short_alive(self, key: str, now: int) -> bool:
        exp = self._short.get(key)
        return exp is not None and now < exp

    def invalidate(self, key: str) -> None:
        self._by_key.pop(key, None)
        self._short.pop(key, None)


class SingleFlight:
    """同键并发去重：只有一个线程真跑，其余等待并共享结果。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[str, threading.Event] = {}
        self._results: dict[str, object] = {}
        self.executed = 0  # 供测试观测“真的跑了几次”

    def do(self, key: str, fn: Callable[[], T]) -> T:
        with self._lock:
            ev = self._events.get(key)
            owner = ev is None
            if owner:
                ev = threading.Event()
                self._events[key] = ev
        assert ev is not None
        if not owner:
            ev.wait()
            return self._results[key]  # type: ignore[return-value]

        try:
            with self._lock:
                self.executed += 1
            result = fn()
            self._results[key] = result
            return result
        finally:
            ev.set()
            with self._lock:
                self._events.pop(key, None)

    def reset(self) -> None:
        self._results.clear()
        self._events.clear()
        self.executed = 0
