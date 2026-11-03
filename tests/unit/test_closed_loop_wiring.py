"""B12.7b 接线测试：闭环 / todo / plan 真的进了决策路径。

这一批补的洞是：那三层都实现了、都测了，但 ``AgentLoop`` **从来没调用过它们**。
没有调用方的强制，就只是一套很好看的规则。

三条断言：
- 有 pending lead 时，决策**必须**先验证它（不能先打别的）；
- 没有 pending lead 时，行为与接线前**逐字相同**（不许顺手改判据）；
- 收尾要**两条都过**：线索闭环 + todo 一致。
"""

from __future__ import annotations

import pytest

from veripen_core.agent.closed_loop import (
    ClosedLoop,
    next_oracle_with_lead_priority,
    run_with_closed_loop,
)
from veripen_core.agent.loop import AgentState, pick_next
from veripen_core.leads import LeadLedger, LeadState
from veripen_core.store import Store
from veripen_core.workbench.plan import TodoItem, TodoList, TodoState

TASK = "t-1"


@pytest.fixture()
def ledger() -> LeadLedger:
    return LeadLedger(Store(":memory:").init())


def _lead(ledger: LeadLedger, *, vb=("idor_cross_account",), conf=0.5):  # noqa: ANN001, ANN202
    return ledger.record(
        task_id=TASK, asset_id="a-1", source_action="http_fuzz", raw_ref="out/act/x.json",
        summary="可疑差分", verifiable_by=vb, confidence_hint=conf,
    )


def _state(**kw) -> AgentState:  # noqa: ANN003
    base = {"pending": (), "strong": (), "only_weak": False, "budget_ratio": 0.1,
            "wave_order": ("s", "t")}
    base.update(kw)
    return AgentState(**base)  # type: ignore[arg-type]


# ══════════════════════════════════════════════════════════════════
# 1 有 pending lead → 必须先验证它
# ══════════════════════════════════════════════════════════════════
def test_pending_lead_overrides_next_rules(ledger: LeadLedger) -> None:
    """这是接线前做不到的事：NEXT_RULES 会先去打 wave_order 里的 s。"""
    lead = _lead(ledger, vb=("idor_cross_account",))
    assert pick_next(_state())[0] == "s"  # 接线前的行为

    step = ClosedLoop(ledger, task_id=TASK).next_step(_state())
    assert step.call.name == "run_oracle"
    assert step.call.args["oracle_id"] == "idor_cross_account"
    assert step.reason == "forced_by_pending_lead"
    assert step.forced_by_lead == lead.lead_id


def test_no_pending_lead_falls_back_to_same_rules(ledger: LeadLedger) -> None:
    """没有线索时，行为必须与接线前**逐字相同**。"""
    st = _state()
    step = ClosedLoop(ledger, task_id=TASK).next_step(st)
    assert step.call.args["oracle_id"] == pick_next(st)[0]
    assert step.reason == pick_next(st)[1]


def test_expired_lead_does_not_force(ledger: LeadLedger) -> None:
    _lead(ledger, vb=())  # 无验证器 → 直接 expired
    step = ClosedLoop(ledger, task_id=TASK).next_step(_state())
    assert step.reason != "forced_by_pending_lead"


def test_forced_choice_wins_over_pending_queue(ledger: LeadLedger) -> None:
    """连 NEXT_RULES 的第一条 resume_pending 也不能压过闭环。"""
    _lead(ledger)
    st = _state(pending=("mass_assignment",))
    assert pick_next(st) == ("mass_assignment", "resume_pending")
    step = ClosedLoop(ledger, task_id=TASK).next_step(st)
    assert step.call.args["oracle_id"] == "idor_cross_account"


def test_pure_function_variant_agrees_with_object_variant(ledger: LeadLedger) -> None:
    _lead(ledger)
    st = _state()
    a = ClosedLoop(ledger, task_id=TASK).next_step(st)
    b, why = next_oracle_with_lead_priority(st, ledger.pending(TASK))
    assert a.call.args["oracle_id"] == b
    assert why.startswith("forced_by_lead:")


# ══════════════════════════════════════════════════════════════════
# 2 todo 同步
# ══════════════════════════════════════════════════════════════════
def test_sync_todo_marks_first_lead_in_progress(ledger: LeadLedger) -> None:
    _lead(ledger, vb=("idor_cross_account",))
    _lead(ledger, vb=("sqli_bool_diff",))
    todo = ClosedLoop(ledger, task_id=TASK).sync_todo()
    assert len(todo.items_list()) == 2
    assert len(todo.in_progress) == 1
    # 一致性因此成立
    assert ClosedLoop(ledger, task_id=TASK, todo=todo).may_finish()[0] is False  # 还有 pending


def test_sync_todo_empty_when_no_leads(ledger: LeadLedger) -> None:
    todo = ClosedLoop(ledger, task_id=TASK).sync_todo()
    assert todo.items_list() == []


# ══════════════════════════════════════════════════════════════════
# 3 收尾：两条都得过
# ══════════════════════════════════════════════════════════════════
def test_cannot_finish_with_pending_lead(ledger: LeadLedger) -> None:
    _lead(ledger)
    ok, why = ClosedLoop(ledger, task_id=TASK).may_finish()
    assert ok is False and "pending_leads" in why


def test_cannot_finish_when_todo_inconsistent(ledger: LeadLedger) -> None:
    """线索都闭环了，但 todo 里还挂着一条 in_progress——两份账本说法不同。"""
    lead = _lead(ledger)
    ledger.supersede(lead.lead_id, state=LeadState.VERIFIED)
    todo = TodoList()
    todo.write([TodoItem(id="lead-x", text="挂在已闭环 lead 上", state=TodoState.IN_PROGRESS,
                         lead_id=lead.lead_id)])
    ok, why = ClosedLoop(ledger, task_id=TASK, todo=todo).may_finish()
    assert ok is False
    assert "不同步" in why


def test_may_finish_when_both_clean(ledger: LeadLedger) -> None:
    lead = _lead(ledger)
    ledger.supersede(lead.lead_id, state=LeadState.REFUTED)
    closed = ClosedLoop(ledger, task_id=TASK)
    assert closed.may_finish() == (True, "")
    assert closed.finish_call("done").name == "finish"


def test_finish_call_raises_when_not_ready(ledger: LeadLedger) -> None:
    _lead(ledger)
    with pytest.raises(RuntimeError, match="不能收尾"):
        ClosedLoop(ledger, task_id=TASK).finish_call()


def test_plain_todo_item_allows_finish(ledger: LeadLedger) -> None:
    """无 lead 背书的普通计划项在无 pending 线索时合法。"""
    todo = TodoList()
    todo.write([TodoItem(id="a", text="整理笔记", state=TodoState.IN_PROGRESS)])
    assert ClosedLoop(ledger, task_id=TASK, todo=todo).may_finish() == (True, "")


# ══════════════════════════════════════════════════════════════════
# 4 跑一段：trace 可复现
# ══════════════════════════════════════════════════════════════════
def test_trace_is_reproducible(ledger: LeadLedger) -> None:
    _lead(ledger, vb=("idor_cross_account",))
    closed = ClosedLoop(ledger, task_id=TASK)
    a = run_with_closed_loop(_state(), closed=closed, execute=lambda c: None, max_steps=3)
    b = run_with_closed_loop(_state(), closed=closed, execute=lambda c: None, max_steps=3)
    assert a.calls() == b.calls()


def test_trace_first_step_is_the_lead(ledger: LeadLedger) -> None:
    _lead(ledger, vb=("idor_cross_account",))
    trace = run_with_closed_loop(_state(), closed=ClosedLoop(ledger, task_id=TASK),
                                 execute=lambda c: None, max_steps=2)
    assert trace.steps[0].call.args["oracle_id"] == "idor_cross_account"


def test_execute_receives_same_calls_as_trace(ledger: LeadLedger) -> None:
    seen: list[str] = []
    trace = run_with_closed_loop(_state(), closed=ClosedLoop(ledger, task_id=TASK),
                                 execute=lambda c: seen.append(c.name), max_steps=3)
    assert len(seen) == len(trace.steps)


def test_plan_state_is_reported(ledger: LeadLedger) -> None:
    step = ClosedLoop(ledger, task_id=TASK, plan_planning=True).next_step(_state())
    assert step.plan_state == "planning"
