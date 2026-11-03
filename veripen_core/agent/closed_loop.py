"""B12.7b · 把线索闭环 / todo / plan 接进 agent 决策路径。

这一批补的是 B12.7、B12.16 都指向的同一个洞：
那三层都实现了、都测了，但 **``AgentLoop`` 从来没调用过它们**。
没有调用方的强制，就只是"有一套很好看的规则"。

接线方式刻意**不动 `NEXT_RULES`**（B10 的稳定件）：在它外面加一层
``pending lead → 强制验证`` 的优先规则。理由：

- ``NEXT_RULES`` 是"打哪个 oracle"的条件表，与"该不该先闭环"是两件事；
- 混在一起改，会让"加一条 rule"和"加一个闭环约束"看起来是同一类改动；
- 单独一层之后，闭环的优先级可以**独立测试**，不用每次重跑整张表。

**这是叠加，不是放宽**：原来选不到的，现在仍然选不到；只是原来会选到别处的，
现在必须先把手上的线索验完。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping, Sequence

from ..leads import LeadLedger, LeadState, can_finish, forced_next_action
from ..tools.registry import ToolCall
from ..workbench.plan import TodoItem, TodoList, TodoState, check_consistency, leads_to_todo
from .loop import AgentState, pick_next


@dataclass(frozen=True)
class NextStep:
    """决策路径的产物：调什么、为什么、以及这一层的自检结果。"""

    call: ToolCall
    reason: str
    forced_by_lead: str | None = None
    plan_state: str = ""
    todo_open: int = 0


@dataclass
class Trace:
    """一次决策的账：**用来证明"判据不变"**（同输入同输出）。"""

    steps: list[NextStep]

    def calls(self) -> list[tuple[str, tuple[tuple[str, str], ...]]]:
        return [
            (s.call.name, tuple(sorted((k, str(v)) for k, v in s.call.args.items())))
            for s in self.steps
        ]


class ClosedLoop:
    """在 ``pick_next`` 外面套一层闭环约束。

    顺序（每一步都只在"上一步没给答案"时才继续）：

    1. **计划模式**：``plan.enter`` 之后只允许只读动作 —— 由 guard 在调用点拒，
       这里只负责把状态带出去（决策层不该重复实现 gate）；
    2. **有 pending lead**：下一动作必须来自它的 ``verifiable_by``；
    3. 否则走原来的 ``NEXT_RULES``；
    4. 都没有 → 重发现（与原行为一致，不硬选）。
    """

    def __init__(
        self,
        ledger: LeadLedger | None = None,
        *,
        task_id: str = "",
        todo: TodoList | None = None,
        plan_planning: bool = False,
        window: Sequence[str] = (),
    ) -> None:
        self.ledger = ledger
        self.task_id = task_id
        self.todo = todo or TodoList()
        self.plan_planning = plan_planning
        self.window = tuple(window)

    # ── 待验证线索 ──────────────────────────────────────────────
    def pending(self) -> list[dict[str, Any]]:
        return self.ledger.pending(self.task_id) if self.ledger else []

    def sync_todo(self) -> TodoList:
        """待验证 lead 自动进 todo，并保证"有线索就有一条 in_progress"。

        为什么要把第一条标 ``in_progress``：``check_consistency`` 要求
        ``pending>0`` ⟺ 有 ``in_progress``。如果只把 lead 堆进 pending，
        下一个 ``todo.list`` 就会看到一堆躺着不动的项——那正是"瞎逛"的样子。
        """
        items = leads_to_todo(self.pending())
        if items:
            first = items[0]
            items[0] = replace(first, state=TodoState.IN_PROGRESS)
        self.todo.write(items)
        return self.todo

    # ── 决策 ────────────────────────────────────────────────────
    def next_step(self, state: AgentState) -> NextStep:
        forced = forced_next_action(self.pending())
        plan_state = "planning" if self.plan_planning else "normal"

        if forced is not None:
            oracle_id, lead_id = forced
            return NextStep(
                call=ToolCall("run_oracle", {"oracle_id": oracle_id, "route_ref": ""}),
                reason="forced_by_pending_lead",
                forced_by_lead=lead_id,
                plan_state=plan_state,
                todo_open=self.todo.open_count,
            )

        picked, why = pick_next(state)
        if picked is None:
            return NextStep(
                call=ToolCall("rediscover_routes", {"reason": "NOT_FOUND"}),
                reason=why or "no_rule",
                plan_state=plan_state,
                todo_open=self.todo.open_count,
            )
        return NextStep(
            call=ToolCall("run_oracle", {"oracle_id": picked, "route_ref": ""}),
            reason=why,
            plan_state=plan_state,
            todo_open=self.todo.open_count,
        )

    # ── 收尾 ────────────────────────────────────────────────────
    def may_finish(self) -> tuple[bool, str]:
        """**两条都得过**：线索闭环 + todo 一致。"""
        pending = len(self.pending())
        ok, why = can_finish(pending)
        if not ok:
            return False, why
        consistent, cwhy = check_consistency(self.todo, pending)
        if not consistent:
            return False, cwhy
        return True, ""

    def finish_call(self, summary: str = "") -> ToolCall:
        ok, why = self.may_finish()
        if not ok:
            raise RuntimeError(f"不能收尾：{why}")
        return ToolCall("finish", {"summary": summary, "unresolved": []})


def run_with_closed_loop(
    state: AgentState,
    *,
    closed: ClosedLoop,
    execute: Callable[[ToolCall], Any],
    max_steps: int = 20,
) -> Trace:
    """用闭环约束跑一段决策，每步都记账。

    ``execute`` 只负责"这一调用得到什么"，**不在这里判好坏**——
    判定是 oracle 的事，这一层只决定"下一个调什么"。
    """
    trace = Trace(steps=[])
    cur = state
    for _ in range(max_steps):
        step = closed.next_step(cur)
        trace.steps.append(step)
        execute(step.call)
        cur = _advance_local(cur, step.call)
        if step.call.name == "finish":
            break
    return trace


# ══════════════════════════════════════════════════════════════════
# 闭环版决策器：让 Loop.run 也吃到约束
# ══════════════════════════════════════════════════════════════════
@dataclass
class ClosedLoopDecider:
    """``Loop.run`` 里用的决策器：**先闭环，再谈打哪个**。

    原来 ``ScriptedDecider`` 直接读 ``NEXT_RULES``，于是 B12.7 的强制闭环
    在真跑分路径上是死代码。这里把它套上，有两个清楚的退路：

    - 想验的 oracle 已经全跑完（``pick_next`` 返回 ``None``）→ 走 ``finish``，
      但**必须先过 ``may_finish``**：线索没闭环就不许收工；
    - 收不了尾 → ``rediscover_routes``，与原来一致，不硬选。

    ``fallback`` 保留原 ``ScriptedDecider`` 的语义：模型挂了流程也得走完。
    """

    closed: ClosedLoop
    name: str = "closed-loop"

    def decide(self, goal: str, state: AgentState, tools: Mapping[str, Any]) -> ToolCall:
        del goal, tools
        step = self.closed.next_step(state)
        if step.call.name != "rediscover_routes":
            return step.call

        # NEXT_RULES 已经没得选了：能收尾就收，不能就重发现
        ok, _why = self.closed.may_finish()
        if ok:
            return self.closed.finish_call("NEXT_RULES 耗尽且线索已闭环")
        return ToolCall("rediscover_routes", {"reason": "LEADS_PENDING"})


def _advance_local(state: AgentState, call: ToolCall) -> AgentState:
    """只推进"这个 oracle 试过了"这一件事；不碰判定。"""
    if call.name != "run_oracle":
        return state
    oid = str(call.args.get("oracle_id") or "")
    return AgentState(
        pending=tuple(o for o in state.pending if o != oid),
        strong=tuple(o for o in state.strong if o != oid),
        only_weak=state.only_weak,
        budget_ratio=state.budget_ratio,
        wave_order=tuple(o for o in state.wave_order if o != oid),
    )


def next_oracle_with_lead_priority(
    state: AgentState, pending_leads: Sequence[Mapping[str, Any]]
) -> tuple[str | None, str]:
    """纯函数版的接线：给 ``pick_next`` 加一条前置规则。

    与 ``ClosedLoop`` 同语义，但不依赖 ledger 对象——
    方便在只有 lead 列表的地方用（例如 bench 的 scripted 路径）。
    """
    live = [p for p in pending_leads if p.get("state") == LeadState.PENDING.value]
    if live:
        best = sorted(live, key=lambda p: (-float(p.get("confidence_hint") or 0),
                                           str(p.get("id") or "")))[0]
        oracles = best.get("verifiable_by") or []
        if isinstance(oracles, str):
            import json

            try:
                oracles = json.loads(oracles)
            except ValueError:
                oracles = []
        if oracles:
            return str(oracles[0]), f"forced_by_lead:{str(best.get('id') or '')[:8]}"
    return pick_next(state)


__all__ = [
    "ClosedLoop",
    "ClosedLoopDecider",
    "NextStep",
    "Trace",
    "next_oracle_with_lead_priority",
    "run_with_closed_loop",
]
