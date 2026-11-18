"""预算硬刹车（08 §3）：不是提醒，是**出口**。

五种预算，各自 80% / 100% 行为不同：

| 预算 | 80% | 100% |
|---|---|---|
| steps | 禁 rediscover | 停循环 |
| tokens | 工具输出改摘要 | 最后一轮只允许 finish |
| wall_clock | 剩余按预计最短排序 | 停 |
| cost_usd | 降级模型 | 同 tokens |
| rediscovery | 只对已产出 finding 的绑定重发现 | 停止重发现 |

设计要点：
- 纯计数与阈值比较，**不读时钟**：`tick(now)` 由调用方给时间；
- 超预算必须**如实标注**（`budget_exhausted ∈ {steps,tokens,wall_clock,cost,rediscovery}`），
  不许把"没跑完"写成"未发现"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class BudgetKind(str, Enum):
    STEPS = "steps"
    TOKENS = "tokens"
    WALL_CLOCK = "wall_clock"
    COST = "cost"
    REDISCOVERY = "rediscovery"


class ExhaustedAction(str, Enum):
    """100% 之后的出口（04 §7.1）。默认 stop——本期任务短，"暂停等人"的收益小于多一个状态。"""

    STOP = "stop"
    PAUSE = "pause"


#: 默认额度（08 §3）
DEFAULTS: dict[BudgetKind, float] = {
    BudgetKind.STEPS: 120,
    BudgetKind.TOKENS: 400_000,
    BudgetKind.WALL_CLOCK: 1800,
    BudgetKind.COST: 5.0,
    BudgetKind.REDISCOVERY: 3,
}


@dataclass(frozen=True)
class BudgetLimits:
    steps: int = 120
    tokens: int = 400_000
    wall_clock_s: int = 1800
    cost_usd: float = 5.0
    rediscovery: int = 3

    def limit(self, kind: BudgetKind) -> float:
        return {
            BudgetKind.STEPS: self.steps,
            BudgetKind.TOKENS: self.tokens,
            BudgetKind.WALL_CLOCK: self.wall_clock_s,
            BudgetKind.COST: self.cost_usd,
            BudgetKind.REDISCOVERY: self.rediscovery,
        }[kind]


@dataclass
class Budget:
    """一次任务的预算账本。所有 increments 都由框架调用，模型不能直接写。"""

    limits: BudgetLimits = field(default_factory=BudgetLimits)
    on_exhausted: ExhaustedAction = ExhaustedAction.STOP
    steps: int = 0
    tokens: int = 0
    elapsed_s: int = 0
    cost_usd: float = 0.0
    rediscovery: int = 0
    #: 谁先打满（报告里的 budget_exhausted）
    exhausted: BudgetKind | None = None

    # ── 读数 ────────────────────────────────────────────────────
    def _used(self, kind: BudgetKind) -> float:
        return {
            BudgetKind.STEPS: self.steps,
            BudgetKind.TOKENS: self.tokens,
            BudgetKind.WALL_CLOCK: self.elapsed_s,
            BudgetKind.COST: self.cost_usd,
            BudgetKind.REDISCOVERY: self.rediscovery,
        }[kind]

    def ratio(self, kind: BudgetKind) -> float:
        limit = self.limits.limit(kind)
        return 0.0 if limit <= 0 else min(self._used(kind) / limit, 1.0)

    def at_80(self, kind: BudgetKind) -> bool:
        return self.ratio(kind) >= 0.8

    def hit(self) -> BudgetKind | None:
        """任一预算到 100% 即返回它（先到者为准，次序固定便于复现）。"""
        for kind in BudgetKind:
            if self.ratio(kind) >= 1.0:
                return kind
        return None

    @property
    def exhausted_kind(self) -> BudgetKind | None:
        return self.exhausted or self.hit()

    # ── 记账 ────────────────────────────────────────────────────
    def spend_step(self) -> None:
        self.steps += 1
        self._settle(BudgetKind.STEPS)

    def spend_tokens(self, n: int) -> None:
        self.tokens += max(n, 0)
        self._settle(BudgetKind.TOKENS)

    def spend_time(self, seconds: int) -> None:
        self.elapsed_s += max(seconds, 0)
        self._settle(BudgetKind.WALL_CLOCK)

    def spend_cost(self, usd: float) -> None:
        self.cost_usd += max(usd, 0.0)
        self._settle(BudgetKind.COST)

    def spend_rediscovery(self) -> None:
        self.rediscovery += 1
        self._settle(BudgetKind.REDISCOVERY)

    def _settle(self, kind: BudgetKind) -> None:
        if self.exhausted is None and self.ratio(kind) >= 1.0:
            self.exhausted = kind

    # ── 80% 行为 ────────────────────────────────────────────────
    def rediscover_allowed(self) -> bool:
        return self.ratio(BudgetKind.REDISCOVERY) < 1.0 and not self.at_80(BudgetKind.STEPS)

    def tool_output_limit(self) -> int:
        """token 预算 80% 后工具输出降到 1KB 摘要（08 §3）。"""
        return 1024 if self.at_80(BudgetKind.TOKENS) else 4096

    def only_finish(self) -> bool:
        """100% 之后只允许 finish / 收尾动作。"""
        return self.hit() is not None

    def model_tier(self) -> str:
        """cost 80% 时降级到便宜模型（降级链由调用方实现，这里只给档位）。"""
        return "cheap" if self.at_80(BudgetKind.COST) else "default"

    def as_dict(self) -> dict[str, object]:
        return {
            "steps": self.steps,
            "tokens": self.tokens,
            "elapsed_s": self.elapsed_s,
            "cost_usd": round(self.cost_usd, 6),
            "rediscovery": self.rediscovery,
            "budget_exhausted": self.exhausted_kind.value if self.exhausted_kind else None,
            "on_budget_exhausted": self.on_exhausted.value,
        }


__all__ = [
    "DEFAULTS",
    "Budget",
    "BudgetKind",
    "BudgetLimits",
    "ExhaustedAction",
]
