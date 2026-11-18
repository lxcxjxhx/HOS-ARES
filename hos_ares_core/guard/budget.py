"""预算硬刹车（04 §7）：到 100% 不是提醒，是停。

B3 只落 token/请求两个最直接的计数与“是否耗尽”的判断；
steps / tokens / wall / cost / rediscovery 的完整执行归 B10 的循环。
"""

from __future__ import annotations

from dataclasses import dataclass


class BudgetExhausted(RuntimeError):
    """VP-3002：预算耗尽 → 收敛并标 ``budget_exhausted``。"""

    code = "VP-3002"
    retryable = False


@dataclass
class Counter:
    used: int = 0
    max: int = 0

    @property
    def at_limit(self) -> bool:
        return self.max > 0 and self.used >= self.max

    @property
    def near_limit(self) -> bool:
        return self.max > 0 and self.used >= int(self.max * 0.8)

    def spend(self, n: int = 1) -> None:
        self.used += n


class Budget:
    def __init__(self, requests: int = 200, steps: int = 120) -> None:
        self.requests = Counter(0, requests)
        self.steps = Counter(0, steps)

    @property
    def exhausted(self) -> bool:
        return self.requests.at_limit or self.steps.at_limit

    def check(self) -> None:
        if self.exhausted:
            raise BudgetExhausted("预算耗尽")

    def spend_request(self, n: int = 1) -> None:
        self.check()
        self.requests.spend(n)
