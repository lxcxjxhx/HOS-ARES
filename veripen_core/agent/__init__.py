"""agent 循环（B10）：discovery 三档 + 条件表选面 + 预算硬刹车 + 降级链。

**本包不 import 任何模型 SDK**：模型通过 `Decider` 协议注入。
"""

from __future__ import annotations

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
    classify_discovery,
    pick_next,
)

__all__ = [
    "NEXT_RULES",
    "AgentState",
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
    "ScriptedDecider",
    "classify_discovery",
    "pick_next",
]
