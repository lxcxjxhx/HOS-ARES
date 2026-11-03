"""agent 循环（08 §1）：Oracle 驱动的 ReAct，不是自由 ReAct。

**模型只影响"跑不跑得到"，不影响"判定是什么"，也不影响"地址是哪来的"。**

三段：
1. `discovery`：模型不可见、不可干预（由 guard 触发）；结果分「完整 / 部分 / 全空」三档。
2. `next_pick`：条件表（纯谓词 + 取第一个匹配），不是打分排序。
3. `loop`：`decide → validate → binding → preflight → dispatch → ledger`，预算是硬刹车。

本模块**不 import 任何模型 SDK**：`Decider` 是注入的协议，测试用 scripted decider，
生产接真模型。这样"降级到 scripted planner 仍能完成"是可测的，而不是口号。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Protocol, Sequence

from ..budget import Budget, BudgetKind
from ..models import Verdict
from ..routes.types import RouteBinding, RouteSet
from ..tools.registry import (
    RETIRED_TOOLS,
    TOOL_CALLS_PER_STEP,
    ToolCall,
    ToolResult,
    dispatch,
    validate_call,
)
from .client import ModelClientError, SchemaViolation, to_tool_call

# ══════════════════════════════════════════════════════════════════
# discovery 三档（08 §1.1）
# ══════════════════════════════════════════════════════════════════
class DiscoveryTier(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    EMPTY = "empty"


@dataclass(frozen=True)
class DiscoveryOutcome:
    tier: DiscoveryTier
    bound_oracles: tuple[str, ...]
    blocked: Mapping[str, str]  # oracle_id → reason（BLOCKED 的原因）
    sources_failed: tuple[str, ...] = ()

    @property
    def should_run(self) -> bool:
        """全空立刻收尾：没有面的循环只会把预算烧在无意义的重发现上。"""
        return self.tier is not DiscoveryTier.EMPTY

    def report_line(self) -> str:
        if self.tier is DiscoveryTier.COMPLETE:
            return ""
        if self.tier is DiscoveryTier.EMPTY:
            why = "、".join(self.sources_failed) or "无契约且无观测"
            return f"本次未获得任何可测面（sources_failed=[{why}]）"
        missing = "、".join(sorted(self.blocked))
        return f"未覆盖：{missing}（无对应语义面）"


def classify_discovery(
    *,
    oracle_ids: Sequence[str],
    routeset: RouteSet,
    selectors: Mapping[str, Sequence[str]],
) -> DiscoveryOutcome:
    """按 selector 标签把"有面/无面"分档。**不猜路径**：没绑定的就是没绑定。"""
    tags = {tag for b in routeset.bindings for tag in _tags_of(b)}
    bound: list[str] = []
    blocked: dict[str, str] = {}
    for oid in oracle_ids:
        wanted = set(selectors.get(oid, ()))
        if wanted and wanted & tags:
            bound.append(oid)
        else:
            blocked[oid] = "route_unresolved"
    tier = (
        DiscoveryTier.COMPLETE if not blocked
        else DiscoveryTier.EMPTY if not bound
        else DiscoveryTier.PARTIAL
    )
    return DiscoveryOutcome(
        tier=tier,
        bound_oracles=tuple(bound),
        blocked=blocked,
        sources_failed=tuple(routeset.sources_failed),
    )


def _tags_of(binding: RouteBinding) -> tuple[str, ...]:
    """绑定的标签从 resolver 的 response_shape 传递而来；这里只读不造。"""
    raw = getattr(binding, "semantic_tags", ())
    return tuple(raw) if raw else ()


# ══════════════════════════════════════════════════════════════════
# next_pick 条件表（08 §1.2）
# ══════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class AgentState:
    pending: tuple[str, ...] = ()
    strong: tuple[str, ...] = ()
    only_weak: bool = False
    budget_ratio: float = 0.0
    wave_order: tuple[str, ...] = ()


@dataclass(frozen=True)
class NextPick:
    condition: Callable[[AgentState], bool]
    pick: Callable[[AgentState], str | None]
    reason: str


#: 规则表按优先级排列，取**第一个**条件为真的（不是打分排序：排序会让加一条规则改变全局行为）
NEXT_RULES: tuple[NextPick, ...] = (
    NextPick(lambda s: bool(s.pending), lambda s: s.pending[0], "resume_pending"),
    NextPick(lambda s: bool(s.strong), lambda s: s.strong[0], "strong_evidence_first"),
    NextPick(lambda s: s.budget_ratio > 0.8,
             lambda s: (s.wave_order[0] if s.wave_order else None), "budget_guard"),
    NextPick(lambda s: s.only_weak, lambda s: None, "need_rediscovery"),
    NextPick(lambda s: True, lambda s: (s.wave_order[0] if s.wave_order else None), "default_wave_order"),
)


def pick_next(state: AgentState) -> tuple[str | None, str]:
    """返回 (oracle_id | None, reason)。``None`` 是合法结果 → 触发重发现，不硬选。"""
    for rule in NEXT_RULES:
        if rule.condition(state):
            return rule.pick(state), rule.reason
    return None, "no_rule"


# ══════════════════════════════════════════════════════════════════
# 决策器（注入；生产接模型，测试接脚本）
# ══════════════════════════════════════════════════════════════════
class Decider(Protocol):
    name: str

    def decide(self, goal: str, state: AgentState, tools: Mapping[str, Any]) -> ToolCall: ...


@dataclass
class ScriptedDecider:
    """降级链的末位：按条件表逐个跑，最后 finish。**模型挂了流程也必须走完。**"""

    name: str = "scripted"
    _queue: list[str] = field(default_factory=list)

    def decide(self, goal: str, state: AgentState, tools: Mapping[str, Any]) -> ToolCall:
        del goal, tools
        oracle_id, _reason = pick_next(state)
        if oracle_id is None:
            return ToolCall("rediscover_routes", {"reason": "NOT_FOUND"})
        return ToolCall("run_oracle", {"oracle_id": oracle_id, "route_ref": ""})


class ModelDecider:
    """真模型的外壳：只把"模型返回的结构化调用"转成 ToolCall，不做任何猜测。

    ``client`` 由调用方注入（`client.from_env()` 从环境构造；测试用 `ScriptedClient`）。
    **输出必过 `to_tool_call` 的窄 schema**：多调用/未知工具/字段越界一律
    `SchemaViolation`，由 `Loop._decide` 转成一次 schema 失败回喂、并驱动降级链。
    """

    name = "model"

    def __init__(self, client: Callable[[str, Mapping[str, Any]], Mapping[str, Any]]) -> None:
        self._client = client

    def decide(self, goal: str, state: AgentState, tools: Mapping[str, Any]) -> ToolCall:
        raw = self._client(goal, {"state": state.__dict__, "tools": tools})
        return to_tool_call(raw)


def build_decider(
    *, client: Callable[[str, Mapping[str, Any]], Mapping[str, Any]] | None = None
) -> Decider:
    """组装降级链：**有模型 → 模型优先，scripted 兜底；无模型 → 纯 scripted**。

    这条组装是 B18 的"接线"本体：`ModelDecider` 一直存在，
    但从没有一处代码把它和 `ScriptedDecider` 串成链。
    没有模型时必须**逐字等价于**今天的行为（`A8` 模型不是瓶颈）。
    """
    scripted = ScriptedDecider()
    if client is None:
        return scripted
    return FailoverDecider([ModelDecider(client), scripted])


class FailoverDecider:
    """降级链：主决策器连续 schema 错误 3 次即降级到下一个（08 §2 第 2 条）。"""

    def __init__(self, chain: Sequence[Decider]) -> None:
        if not chain:
            raise ValueError("降级链不能为空")
        self.chain = list(chain)
        self.level = 0
        self.consecutive_errors = 0
        self.name = f"{chain[0].name}->{chain[-1].name}"

    @property
    def current(self) -> Decider:
        return self.chain[self.level]

    def decide(self, goal: str, state: AgentState, tools: Mapping[str, Any]) -> ToolCall:
        return self.current.decide(goal, state, tools)

    def note_result(self, result: ToolResult) -> None:
        if result.error_class == "schema":
            self.consecutive_errors += 1
            if self.consecutive_errors >= 3 and self.level + 1 < len(self.chain):
                self.level += 1
                self.consecutive_errors = 0
        else:
            self.consecutive_errors = 0


# ══════════════════════════════════════════════════════════════════
# 循环
# ══════════════════════════════════════════════════════════════════
class Decision(str, Enum):
    RUN = "run"
    CONVERGED = "converged"
    BUDGET = "budget"
    NOTHING_TO_DO = "nothing_to_do"


@dataclass
class LedgerEntry:
    step: int
    tool: str
    ok: bool
    error_class: str | None
    new_facts: int
    verdict: str | None = None
    capsule_id: str | None = None


@dataclass
class LoopResult:
    decision: Decision
    step_entries: list[LedgerEntry] = field(default_factory=list)
    verdicts: dict[str, str] = field(default_factory=dict)
    blocked: dict[str, str] = field(default_factory=dict)
    budget: dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    @property
    def new_facts(self) -> int:
        return sum(e.new_facts for e in self.step_entries)


class Loop:
    """一步一调用；连续 3 步无新信息即收敛（08 §3 收敛条件 2）。"""

    def __init__(
        self,
        *,
        runtime: Any,
        decider: Decider,
        budget: Budget | None = None,
        goal: str = "",
        max_steps: int | None = None,
        store_dir: str | None = None,
        plan_gate: Any | None = None,
    ) -> None:
        self.runtime = runtime
        self.decider = decider
        self.budget = budget or Budget()
        self.goal = goal
        #: 计划闸门（B12.7c）：给了就在每次 dispatch 前查，
        #: 让「计划模式下不许写」有唯一执行点，而不是靠每个 decider 自觉。
        self.plan_gate = plan_gate
        self.max_steps = max_steps if max_steps is not None else self.budget.limits.steps
        self.store_dir = store_dir
        self.entries: list[LedgerEntry] = []
        self.verdicts: dict[str, str] = {}
        self._idle_streak = 0
        #: `_decide` 捕获的模型异常，`_dispatch` 消费一次后清空。
        self._pending_error: ModelClientError | None = None

    def run(self, state: AgentState) -> LoopResult:
        if not state.pending and not state.strong and not state.wave_order:
            return LoopResult(Decision.NOTHING_TO_DO, [], {}, {}, self.budget.as_dict(), "no_oracles")

        while True:
            exhausted = self.budget.hit()
            if exhausted is not None:
                return self._finish(Decision.BUDGET, reason=f"budget_exhausted:{exhausted.value}")
            if len(self.entries) >= self.max_steps:
                self.budget.exhausted = BudgetKind.STEPS
                return self._finish(Decision.BUDGET, reason="budget_exhausted:steps")
            if self._idle_streak >= 3:
                return self._finish(Decision.CONVERGED, reason="no_new_facts_3_steps")

            call = self._decide(state)
            result = self._note(self._dispatch(call))
            self.budget.spend_step()
            self.entries.append(
                LedgerEntry(
                    step=len(self.entries) + 1,
                    tool=call.name,
                    ok=result.ok,
                    error_class=result.error_class,
                    new_facts=self._facts_from(call, result),
                    verdict=result.data.get("verdict"),
                    capsule_id=result.data.get("capsule_id"),
                )
            )
            if result.error_class == "schema":
                # 模型给的调用不合法 ≠ 任务无进展：不计入 idle，让它有机会自愈/降级
                self._idle_streak = 0
            else:
                self._idle_streak = 0 if self._facts_from(call, result) else self._idle_streak + 1

            if call.name == "finish":
                return self._finish(Decision.CONVERGED, reason="finished")
            state = self._advance(state, call, result)

    # ── 内部 ────────────────────────────────────────────────────
    def _decide(self, state: AgentState) -> ToolCall:
        """调决策器；模型给坏输出时**不崩循环**，而是转成一次 schema 失败回喂。

        `08 §2` 第 2 条：非法调用回喂 ≤3 次后降级。这里只负责把异常
        变成"一次失败的调用"，降级计数交给 `FailoverDecider.note_result`。
        没有这层，接上真模型后第一句不合 schema 就会把整个循环打挂。
        """
        try:
            return self.decider.decide(self.goal, state, {})
        except ModelClientError as exc:
            # 用一条必然被 validate_call 拒的调用表示"这一步模型没给出合法调用"。
            # 不在 loop 里判 schema——那是 tools.registry 的职责，这里只转达。
            self._pending_error = exc
            return ToolCall("__model_error__", {"detail": str(exc)})

    def _note(self, result: ToolResult) -> ToolResult:
        """把结果回告决策器（降级链据此计数）；Decider 不支持就跳过。"""
        note = getattr(self.decider, "note_result", None)
        if callable(note):
            note(result)
        return result

    def _dispatch(self, call: ToolCall) -> ToolResult:
        if call.name == "__model_error__":
            # 模型这一步没给出合法调用：记成 schema 错误（可降级），
            # 不进 dispatch、不碰 runtime。降级链据 error_class=schema 计数。
            err = getattr(self, "_pending_error", None)
            self._pending_error = None
            return ToolResult(
                ok=False,
                error_class="schema",
                hint=f"模型输出不合窄 schema：{err}" if err else "模型输出不合窄 schema",
            )
        checked = validate_call(call)
        if isinstance(checked, ToolResult):
            return checked
        if call.name == "rediscover_routes" and not self.budget.rediscover_allowed():
            return ToolResult(ok=False, error_class="budget", hint="重发现配额已用尽")
        result = dispatch(
            call,
            self.runtime,
            store_dir=self.store_dir,
            plan_gate=self.plan_gate,
        )
        if result.ok and call.name == "run_oracle":
            verdict = str(result.data.get("verdict") or "")
            oracle_id = str(call.args.get("oracle_id") or "")
            if oracle_id and verdict:
                self.verdicts[oracle_id] = verdict
        return result

    def _facts_from(self, call: ToolCall, result: ToolResult) -> int:
        if not result.ok:
            return 0
        if call.name in {"record_note", "finish", "list_routes", "list_assets"}:
            return 0
        if call.name == "run_oracle":
            return 1 if result.data.get("verdict") else 0
        return 1

    def _advance(self, state: AgentState, call: ToolCall, result: ToolResult) -> AgentState:
        if call.name != "run_oracle":
            return state
        oid = str(call.args.get("oracle_id") or "")
        pending = tuple(o for o in state.pending if o != oid)
        strong = tuple(o for o in state.strong if o != oid)
        wave = tuple(o for o in state.wave_order if o != oid)
        if not result.ok and result.error_class == "route":
            # 路由类失败是**唯一**的重试入口：走重发现，不重发
            self._dispatch(ToolCall("rediscover_routes", {"reason": "NOT_FOUND"}))
        return AgentState(
            pending=pending,
            strong=strong,
            only_weak=state.only_weak and not strong,
            budget_ratio=self.budget.ratio(BudgetKind.STEPS),
            wave_order=wave,
        )

    def _finish(self, decision: Decision, *, reason: str) -> LoopResult:
        blocked = {oid: "budget_exhausted" for oid in self._remaining()}
        return LoopResult(
            decision=decision,
            step_entries=list(self.entries),
            verdicts=dict(self.verdicts),
            blocked=blocked,
            budget=self.budget.as_dict(),
            reason=reason,
        )

    def _remaining(self) -> list[str]:
        return [oid for oid in self.verdicts if self.verdicts[oid] in (Verdict.BLOCKED.value, "")]


__all__ = [
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
    "NEXT_RULES",
    "NextPick",
    "RETIRED_TOOLS",
    "ScriptedDecider",
    "TOOL_CALLS_PER_STEP",
    "classify_discovery",
    "build_decider",
    "pick_next",
]
