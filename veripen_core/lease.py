"""租约管理（`04 §2`、`03 §3`）。

核心保证：**worker 崩了不需要他人善后**——租约自带过期。
因此这里没有"释放锁"的复杂路径，只有"续租"和"过期回收"。

时钟策略（`04 §2` 关键细节 #1）：生产用 Redis `TIME`，本地/测试用注入的 `clock`。
不直接调 `time.time()`——时钟漂移是回收误判第一杀手。
"""

from __future__ import annotations

import os
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

from .models import (
    BACKOFF_SCHEDULE_S,
    HEARTBEAT_INTERVAL_S,
    LEASE_TTL_S,
    MAX_ATTEMPTS,
    Task,
    TaskState,
)
from .store import Store


class Clock(Protocol):
    """统一时钟。生产实现走 Redis TIME，测试实现可自由前进。"""

    def now_s(self) -> int: ...


class LocalClock:
    """本地单调时钟（秒）。仅用于单机/测试；多机交付档必须换 RedisClock。"""

    def now_s(self) -> int:
        return int(time.time())


class FrozenClock:
    """测试用：可手动推进的时钟，保证回收测试不依赖 sleep。"""

    def __init__(self, start: int = 1_700_000_000) -> None:
        self._t = start

    def now_s(self) -> int:
        return self._t

    def advance(self, seconds: int) -> None:
        self._t += seconds


def make_worker_id() -> str:
    """`worker_id = host + pid + ulid`（`04 §2`）。"""
    from .models import new_ulid

    return f"{socket.gethostname()}-{os.getpid()}-{new_ulid()[-10:]}"


@dataclass
class LeaseHandle:
    """一个已持有的租约。`renew()` 失败两次即视为丢失（`04 §2`）。"""

    task_id: str
    worker_id: str
    store: Store
    clock: Clock
    ttl_s: int = LEASE_TTL_S
    _consecutive_failures: int = field(default=0, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def renew(self) -> bool:
        """续租。成功后把手里的失败计数清零。

        续租是 `UPDATE ... WHERE id=? AND lease_owner=?`——只有仍持有者能续，
        被别人接走之后续租必然 0 行，这就是"丢失租约"的检测点。
        """
        now = self.clock.now_s()
        with self._lock:
            cur = self.store._conn.execute(  # noqa: SLF001 — 同包内部
                "UPDATE tasks SET lease_until=?, heartbeat_at=?, updated_at=? "
                "WHERE id=? AND lease_owner=? AND state IN ('LEASED','RUNNING')",
                (now + self.ttl_s, now, now, self.task_id, self.worker_id),
            )
            ok = cur.rowcount == 1
            self._consecutive_failures = 0 if ok else self._consecutive_failures + 1
            return ok

    @property
    def lost(self) -> bool:
        """连续 2 次续租失败 = 租约丢失，调用方必须停止（`04 §2`）。"""
        return self._consecutive_failures >= 2

    def release(self) -> bool:
        """SIGTERM 时主动释放：回 QUEUED，`attempt` 不 +1（`04 §2`）。

        为什么主动释放而不是让它过期：让重排快 30 秒，演示效果更好。
        """
        task = self.store.get_task(self.task_id)
        if task is None:
            return False
        if task.state not in (TaskState.LEASED, TaskState.RUNNING):
            return False
        now = self.clock.now_s()
        cur = self.store._conn.execute(  # noqa: SLF001 — 同包内部
            "UPDATE tasks SET state='QUEUED', lease_owner=NULL, lease_until=0, updated_at=? "
            "WHERE id=? AND lease_owner=? AND state IN ('LEASED','RUNNING')",
            (now, self.task_id, self.worker_id),
        )
        return cur.rowcount == 1


class HeartbeatThread:
    """独立心跳线程，每 `HEARTBEAT_INTERVAL_S` 续租一次（`04 §2`）。

    注意 `04 §2` 的另一条：任务内长操作还要每 10s 主动续租——
    因为心跳线程可能被 GIL/阻塞 IO 饿死。`renew_now()` 就是给那条用的。
    """

    def __init__(self, lease: LeaseHandle, interval_s: int = HEARTBEAT_INTERVAL_S) -> None:
        self.lease = lease
        self.interval_s = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.lost_event = threading.Event()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="vp-heartbeat", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            if not self.lease.renew():
                if self.lease.lost:
                    # 已丢失：置位让主循环退出（`04 §2` 要求 os._exit，由调用方决定）
                    self.lost_event.set()
                    return

    def renew_now(self) -> bool:
        """长操作中途主动续租（避免心跳线程被饿死）。"""
        return self.lease.renew()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval_s + 1)
            self._thread = None


@dataclass(frozen=True)
class Backoff:
    """退避策略：1s/4s/16s ±20%（`04 §3`）。抖动防止 3 个 worker 同步重试打崩目标。"""

    attempt: int
    jitter_ratio: float = 0.2
    rng: Callable[[], float] = field(default=lambda: 0.5)

    def seconds(self) -> float:
        if self.attempt < 0:
            raise ValueError("attempt must be >= 0")
        idx = min(self.attempt, len(BACKOFF_SCHEDULE_S) - 1)
        base = BACKOFF_SCHEDULE_S[idx]
        # rng 返回 [0,1) → 映射到 ±jitter_ratio
        factor = 1.0 + (self.rng() * 2 - 1) * self.jitter_ratio
        return max(0.0, base * factor)

    @property
    def exhausted(self) -> bool:
        return self.attempt >= MAX_ATTEMPTS
