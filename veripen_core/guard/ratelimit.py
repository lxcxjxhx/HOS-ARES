"""令牌桶与每资产并发（`05 §2`、`04 §5`）。

**关键纪律**：令牌桶不可用本地内存实现。
3 个 worker 各自 20 QPS = 实际 60 QPS，直接违反合同（`05 §2`）。

所以本模块的 `TokenBucket` 是**接口 + 本地实现 + Redis 实现**：
  - `LocalTokenBucket`：单机/测试用，进程内，明确标注不可用于多 worker
  - `RedisTokenBucket`：生产用，Lua 原子脚本（`05 §2` 给的 LUA）

`get_backend()` 按是否有 Redis 连接自动选，选本地时打警告。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Protocol

from ..errors import ERR_THROTTLE_TIMEOUT, VeripenError

#: `05 §2` 的 Lua 脚本，逐字实现（`TIME` 取服务端时间，避免客户端时钟漂移）
REDIS_LUA = """
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local b = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(b[1])
local ts = tonumber(b[2])
if tokens == nil then tokens = tonumber(ARGV[1]) end
if ts == nil then ts = now end
tokens = math.min(tonumber(ARGV[1]), tokens + (now - ts) * tonumber(ARGV[2]))
if tokens < 1 then
  redis.call('HMSET', KEYS[1], 'tokens', tokens, 'ts', now)
  return 0
end
redis.call('HMSET', KEYS[1], 'tokens', tokens - 1, 'ts', now)
return 1
"""

#: `04 §5` 全局默认
DEFAULT_QPS = 20.0
DEFAULT_BURST = 40
DEFAULT_MAX_WAIT_S = 5.0
DEFAULT_PER_ASSET_CONCURRENCY = 2

#: 拿不到令牌时的重试间隔（`05 §2` "短暂 sleep(50ms) 重试"）
RETRY_SLEEP_S = 0.05


class BucketUnavailable(RuntimeError):
    """桶后端不可用。调用方必须 fail-closed（不放行），不是"降级为不限速"。"""


class TokenBucket(Protocol):
    """令牌桶接口。`try_acquire()` 非阻塞返回 True/False。"""

    def try_acquire(self, tokens: int = 1) -> bool: ...

    def available(self) -> float: ...


class LocalTokenBucket:
    """进程内令牌桶。

    **只适用于单 worker**。多 worker 时每个进程各有一份桶，
    合起来就是 N × QPS，直接违反 `05 §2` 的合同。
    """

    backend_name = "local-single-worker-only"

    def __init__(
        self,
        rate: float = DEFAULT_QPS,
        burst: int = DEFAULT_BURST,
        *,
        clock=time.monotonic,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate must be > 0")
        self.rate = float(rate)
        self.burst = int(burst)
        self._tokens = float(burst)
        self._last = clock()
        self._clock = clock
        self._lock = threading.Lock()

    def try_acquire(self, tokens: int = 1) -> bool:
        with self._lock:
            now = self._clock()
            elapsed = max(0.0, now - self._last)
            self._tokens = min(float(self.burst), self._tokens + elapsed * self.rate)
            self._last = now
            if self._tokens < tokens:
                return False
            self._tokens -= tokens
            return True

    def available(self) -> float:
        with self._lock:
            now = self._clock()
            elapsed = max(0.0, now - self._last)
            return min(float(self.burst), self._tokens + elapsed * self.rate)


class RedisTokenBucket:
    """Redis 实现（生产）。Lua 脚本保证原子，走服务端 `TIME`。

    注意：构造时**不**连接；第一次 `try_acquire()` 才连，连接失败抛
    `BucketUnavailable`，调用方 fail-closed。
    """

    backend_name = "redis"

    def __init__(
        self,
        redis_client,
        key: str = "vp:rl:global",
        rate: float = DEFAULT_QPS,
        burst: int = DEFAULT_BURST,
    ) -> None:
        self._redis = redis_client
        self._key = key
        self.rate = float(rate)
        self.burst = int(burst)
        self._script = None

    def _ensure_script(self):
        if self._script is None:
            try:
                self._script = self._redis.register_script(REDIS_LUA)
            except Exception as exc:  # noqa: BLE001 — 后端异常统一转 fail-closed
                raise BucketUnavailable(f"redis script registration failed: {exc}") from exc
        return self._script

    def try_acquire(self, tokens: int = 1) -> bool:
        script = self._ensure_script()
        try:
            result = script(keys=[self._key], args=[self.burst, self.rate], client=self._redis)
        except Exception as exc:  # noqa: BLE001
            raise BucketUnavailable(f"redis token bucket failed: {exc}") from exc
        return int(result) == 1

    def available(self) -> float:
        try:
            vals = self._redis.hmget(self._key, "tokens", "ts")
        except Exception as exc:  # noqa: BLE001
            raise BucketUnavailable(f"redis read failed: {exc}") from exc
        if not vals or vals[0] is None:
            return float(self.burst)
        return float(vals[0])


@dataclass
class ThrottleResult:
    """一次限流等待的结果，用于 `task_events` 的 `throttle` 事件。"""

    granted: bool
    waited_s: float
    attempts: int


@dataclass
class RateLimiter:
    """三层限流的第 1、2 层（`04 §5`）。

    第 1 层：全局令牌桶（20/s, burst 40）
    第 2 层：每资产并发信号量（2）

    第 3 层（目标自适应降速）在 `TargetBackoff` 里，与这里解耦。
    """

    bucket: TokenBucket
    per_asset_concurrency: int = DEFAULT_PER_ASSET_CONCURRENCY
    max_wait_s: float = DEFAULT_MAX_WAIT_S
    retry_sleep_s: float = RETRY_SLEEP_S
    _semaphores: dict[str, threading.Semaphore] = field(default_factory=dict, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def _sem(self, asset_id: str) -> threading.Semaphore:
        with self._lock:
            sem = self._semaphores.get(asset_id)
            if sem is None:
                sem = threading.Semaphore(self.per_asset_concurrency)
                self._semaphores[asset_id] = sem
            return sem

    def acquire(self, *, asset_id: str, sleep=time.sleep, clock=time.monotonic) -> ThrottleResult:
        """排队等令牌（**不丢弃**，`04 §5`）。

        拿不到就短暂 sleep 重试，累计超过 `max_wait_s` 才放弃并抛
        `VP-3003`（该步 INCONCLUSIVE，任务继续）。
        """
        start = clock()
        attempts = 0
        while True:
            attempts += 1
            if self.bucket.try_acquire():
                waited = clock() - start
                return ThrottleResult(granted=True, waited_s=waited, attempts=attempts)
            if clock() - start >= self.max_wait_s:
                raise VeripenError(
                    ERR_THROTTLE_TIMEOUT,
                    "throttle wait exceeded",
                    detail={"asset_id": asset_id, "waited_s": round(clock() - start, 3), "attempts": attempts},
                    hint="降低 profile 的 qps 或提高 max_throttle_wait_s",
                )
            sleep(self.retry_sleep_s)

    def asset_slot(self, asset_id: str) -> threading.Semaphore:
        """取该资产的并发信号量。`with limiter.asset_slot(aid): ...`"""
        return self._sem(asset_id)

    def release_all(self, asset_id: str) -> None:
        """测试/清理用：丢弃该资产的信号量状态。"""
        with self._lock:
            self._semaphores.pop(asset_id, None)


@dataclass
class TargetBackoff:
    """第 3 层：目标自适应降速（`04 §5`）。

    规则：
      - 目标 429 → 全局降速 30%，持续 60s
      - 目标 5xx 连续 5 次 → 该资产熔断 5 分钟
    """

    base_rate: float = DEFAULT_QPS
    rate_factor: float = 1.0
    backoff_until: float = 0.0
    consecutive_5xx: dict[str, int] = field(default_factory=dict)
    circuit_until: dict[str, float] = field(default_factory=dict)
    circuit_threshold: int = 5
    circuit_seconds: float = 300.0
    backoff_seconds: float = 60.0

    def effective_rate(self, now: float) -> float:
        factor = self.rate_factor if now < self.backoff_until else 1.0
        return max(1.0, self.base_rate * factor)  # 最低 1/s

    def observe_429(self, now: float) -> None:
        """收到 429：降速 30%，持续 60s。"""
        self.rate_factor = max(0.1, self.rate_factor * 0.7)
        self.backoff_until = now + self.backoff_seconds

    def observe_status(self, asset_id: str, status: int, now: float) -> None:
        """按状态码更新熔断/降速状态。"""
        if status == 429:
            self.observe_429(now)
            self.consecutive_5xx[asset_id] = 0
        elif 500 <= status < 600:
            n = self.consecutive_5xx.get(asset_id, 0) + 1
            self.consecutive_5xx[asset_id] = n
            if n >= self.circuit_threshold:
                self.circuit_until[asset_id] = now + self.circuit_seconds
                self.consecutive_5xx[asset_id] = 0
        else:
            self.consecutive_5xx[asset_id] = 0

    def circuit_open(self, asset_id: str, now: float) -> bool:
        until = self.circuit_until.get(asset_id, 0.0)
        return now < until


def get_backend(redis_client=None, *, rate: float = DEFAULT_QPS, burst: int = DEFAULT_BURST) -> TokenBucket:
    """按可用性选后端。有 Redis 用 Redis，没有则用本地并警告不可用于多 worker。"""
    if redis_client is not None:
        return RedisTokenBucket(redis_client, rate=rate, burst=burst)
    import warnings

    warnings.warn(
        "Using LocalTokenBucket: only valid for a single worker. "
        "Multiple workers each get their own bucket, which multiplies the effective QPS (05 §2).",
        RuntimeWarning,
        stacklevel=2,
    )
    return LocalTokenBucket(rate=rate, burst=burst)
