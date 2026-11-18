"""令牌桶（04 §5.1 / 05 §2）：全局速率，**排队而非丢弃**。

本实现是单进程内的线程安全桶；多 worker 场景由 B13 换成 Redis Lua 实现，
接口（``acquire`` / ``try_acquire``）保持不变——**不可用本地内存冒充集群级限流**。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ThrottleConfig:
    rate_per_s: float = 20.0
    burst: float = 40.0
    max_wait_s: float = 5.0
    poll_s: float = 0.05


from .errors import ThrottleTimeout  # 唯一错误类型：16 处异常类只定义一次（24 §4）


class TokenBucket:
    def __init__(self, cfg: ThrottleConfig | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        self.cfg = cfg or ThrottleConfig()
        self._clock = clock
        self._tokens = self.cfg.burst
        self._ts = clock()
        self._lock = threading.Lock()

    def _refill(self, now: float) -> None:
        elapsed = max(0.0, now - self._ts)
        self._tokens = min(self.cfg.burst, self._tokens + elapsed * self.cfg.rate_per_s)
        self._ts = now

    def try_acquire(self, tokens: float = 1.0) -> bool:
        with self._lock:
            self._refill(self._clock())
            if self._tokens < tokens:
                return False
            self._tokens -= tokens
            return True

    def acquire(self, tokens: float = 1.0) -> float:
        """拿到令牌返回等待秒数；超 ``max_wait_s`` 抛 ``ThrottleTimeout``。"""
        started = self._clock()
        while True:
            if self.try_acquire(tokens):
                return float(self._clock() - started)
            if self._clock() - started >= self.cfg.max_wait_s:
                raise ThrottleTimeout(f"限流排队超过 {self.cfg.max_wait_s}s")
            time.sleep(self.cfg.poll_s)

    @property
    def tokens(self) -> float:
        with self._lock:
            self._refill(self._clock())
            return self._tokens
