"""agent 循环（B10）：discovery 三档 + 条件表选面 + 预算硬刹车 + 降级链。

**本包不 import 任何模型 SDK**：模型通过 `Decider` 协议注入。

B12.7b/7c 增补：`ClosedLoop` / `ClosedLoopDecider` 在条件表外面加一层
「有 pending lead 必须先验它」。它是**叠加**，不是改判据——没有 lead 时
`pick_next` 的结果逐字不变。
"""

from __future__ import annotations

from .closed_loop import (
    ClosedLoop,
    ClosedLoopDecider,
    NextStep,
    Trace,
    next_oracle_with_lead_priority,
    run_with_closed_loop,
)
from .loop import (
    NEXT_RULES,
    AgentState,
    Decider,
    Decision,
    DiscoveryOutcome,
    DiscoveryTier,
    FailoverDecider,
    LedgerEntry,
    Loop,
    LoopResult,
    ModelDecider,
    NextPick,
    ScriptedDecider,
    build_decider,
    classify_discovery,
    pick_next,
)

__all__ = [
    "NEXT_RULES",
    "AgentState",
    "ClosedLoop",
    "ClosedLoopDecider",
    "Decider",
    "Decision",
    "DiscoveryOutcome",
    "DiscoveryTier",
    "FailoverDecider",
    "LedgerEntry",
    "Loop",
    "LoopResult",
    "ModelDecider",
    "NextPick",
    "NextStep",
    "ScriptedDecider",
    "Trace",
    "build_decider",
    "classify_discovery",
    "next_oracle_with_lead_priority",
    "pick_next",
    "run_with_closed_loop",
]
