"""B12.16 计划与任务层测试（38 §5，21 §B12.16）。

三条 DoD：
- ``plan.enter`` 后 ``fs.write`` 被拒；
- ``plan.exit`` 未确认不放行；
- ``todo`` 与 ``leads`` 一致（``pending>0`` ⟺ 有 ``in_progress``）。

第三条是双向的。只查一边会漏掉另一半：
有线索没在闭环（瞎逛），或没线索却在忙（在做查不到出处的事）。
"""

from __future__ import annotations

import pytest

from veripen_core.workbench.plan import (
    PLAN_BLOCKED_OPS,
    PLAN_SAFE_OPS,
    PlanError,
    PlanGate,
    PlanState,
    TodoItem,
    TodoList,
    TodoState,
    check_consistency,
    guard_workbench_op,
    leads_to_todo,
)
from veripen_core.workbench.shell import ApprovalPolicy, SandboxMode

GOOD = (SandboxMode.WORKSPACE_WRITE, ApprovalPolicy.ON_RISK)


def _planning() -> PlanGate:
    g = PlanGate()
    g.enter(reason="先摸清结构")
    return g


def _exited() -> PlanGate:
    g = _planning()
    g.exit(plan_text="先读契约再打", actions=("fs.read",), risks=("可能超预算",))
    return g


# ══════════════════════════════════════════════════════════════════
# 1 plan.enter 后 fs.write 被拒
# ══════════════════════════════════════════════════════════════════
def test_plan_enter_blocks_fs_write() -> None:
    """DoD 点名：plan.enter 后 fs.write 被拒。"""
    g = _planning()
    with pytest.raises(PlanError, match="计划模式下不允许 fs.write"):
        g.check("fs.write")


def test_plan_enter_blocks_all_write_and_exec_ops() -> None:
    g = _planning()
    for op in sorted(PLAN_BLOCKED_OPS):
        with pytest.raises(PlanError, match="计划模式下不允许"):
            g.check(op)


def test_plan_enter_allows_read_only_ops() -> None:
    g = _planning()
    for op in sorted(PLAN_SAFE_OPS):
        g.check(op)  # 不抛


def test_safe_and_blocked_lists_do_not_overlap() -> None:
    assert not (PLAN_SAFE_OPS & PLAN_BLOCKED_OPS)


def test_normal_state_allows_write() -> None:
    PlanGate().check("fs.write")  # 不抛


def test_enter_requires_reason() -> None:
    with pytest.raises(PlanError, match="需要理由"):
        PlanGate().enter(reason="  ")


def test_cannot_enter_twice() -> None:
    g = _planning()
    with pytest.raises(PlanError, match="不能重复进入"):
        g.enter(reason="再来一次")


# ══════════════════════════════════════════════════════════════════
# 2 plan.exit 未确认不放行
# ══════════════════════════════════════════════════════════════════
def test_exit_enters_awaiting_approval_not_unlocked() -> None:
    """plan.exit 只是"提交计划"，不是"批准自己"。"""
    g = _exited()
    assert g.state is PlanState.AWAITING_APPROVAL
    assert g.unlocked is False
    with pytest.raises(PlanError, match="计划模式下不允许 fs.write"):
        g.check("fs.write")


def test_approval_unlocks() -> None:
    g = _exited()
    g.approve(by="secops_lead")
    assert g.state is PlanState.NORMAL
    assert g.unlocked is True
    g.check("fs.write")  # 不抛


def test_approval_requires_approver_name() -> None:
    g = _exited()
    with pytest.raises(PlanError, match="谁批的"):
        g.approve(by="")


def test_exit_requires_actions_and_risks() -> None:
    g = _planning()
    with pytest.raises(PlanError, match="至少一条 action"):
        g.exit(plan_text="x", actions=(), risks=("r",))
    g2 = _planning()
    with pytest.raises(PlanError, match="至少一条 risk"):
        g2.exit(plan_text="x", actions=("fs.read",), risks=())


def test_exit_requires_plan_text() -> None:
    with pytest.raises(PlanError, match="plan_text"):
        _planning().exit(plan_text="  ", actions=("a",), risks=("r",))


def test_exit_only_from_planning() -> None:
    with pytest.raises(PlanError, match="只在 planning 状态可用"):
        PlanGate().exit(plan_text="x", actions=("a",), risks=("r",))


def test_abort_returns_to_normal_without_plan() -> None:
    g = _exited()
    g.abort()
    assert g.state is PlanState.NORMAL
    assert g.plan_text == ""


def test_approve_without_pending_plan_fails() -> None:
    with pytest.raises(PlanError, match="没有待确认的计划"):
        PlanGate().approve(by="x")


# ══════════════════════════════════════════════════════════════════
# 3 todo 基本纪律
# ══════════════════════════════════════════════════════════════════
def _todo(*items: TodoItem) -> TodoList:
    t = TodoList()
    t.write(items)
    return t


def test_write_is_full_replacement() -> None:
    t = _todo(TodoItem(id="a", text="第一步"))
    t.write([TodoItem(id="b", text="换掉了")])
    assert [i.id for i in t.items_list()] == ["b"]


def test_write_rejects_duplicate_ids() -> None:
    with pytest.raises(PlanError, match="id 重复"):
        _todo(TodoItem(id="a", text="x"), TodoItem(id="a", text="y"))


def test_write_allows_at_most_one_in_progress() -> None:
    with pytest.raises(PlanError, match="一次只做一件事"):
        _todo(TodoItem(id="a", text="x", state=TodoState.IN_PROGRESS),
              TodoItem(id="b", text="y", state=TodoState.IN_PROGRESS))


def test_update_requires_change() -> None:
    t = _todo(TodoItem(id="a", text="x"))
    with pytest.raises(PlanError, match="必须产生变化"):
        t.update("a", state=TodoState.PENDING)


def test_done_requires_evidence() -> None:
    """「做完了」要有东西能看。"""
    t = _todo(TodoItem(id="a", text="x", state=TodoState.IN_PROGRESS))
    with pytest.raises(PlanError, match="evidence_ref"):
        t.update("a", state=TodoState.DONE)
    got = t.update("a", state=TodoState.DONE, evidence_ref="capsule:abc")
    assert got.evidence_ref == "capsule:abc"


def test_update_unknown_id_fails() -> None:
    with pytest.raises(PlanError, match="不存在"):
        _todo().update("nope", state=TodoState.DONE, evidence_ref="x")


def test_counts_and_open_count() -> None:
    t = _todo(
        TodoItem(id="a", text="1", state=TodoState.DONE, evidence_ref="e"),
        TodoItem(id="b", text="2", state=TodoState.PENDING),
        TodoItem(id="c", text="3", state=TodoState.BLOCKED),
    )
    assert t.counts() == {"pending": 1, "in_progress": 0, "done": 1, "blocked": 1}
    assert t.open_count == 1  # blocked 不算 open


# ══════════════════════════════════════════════════════════════════
# 4 leads → todo
# ══════════════════════════════════════════════════════════════════
def test_pending_leads_become_todo_items() -> None:
    leads = [
        {"id": "L" * 20, "verifiable_by": ["idor_cross_account"]},
        {"id": "M" * 20, "verifiable_by": '["sqli_bool_diff"]'},
    ]
    items = leads_to_todo(leads)
    assert len(items) == 2
    assert all(i.state is TodoState.PENDING for i in items)
    assert all(i.lead_id for i in items)
    assert items == sorted(items, key=lambda i: i.id)


def test_leads_to_todo_handles_empty_and_malformed() -> None:
    assert leads_to_todo([]) == []
    assert leads_to_todo([{"id": ""}]) == []
    got = leads_to_todo([{"id": "X" * 20, "verifiable_by": "not-json"}])
    assert "（无）" in got[0].text


# ══════════════════════════════════════════════════════════════════
# 5 一致性：pending>0 ⟺ 有 in_progress（双向）
# ══════════════════════════════════════════════════════════════════
def test_consistency_ok_when_lead_pending_and_in_progress() -> None:
    t = _todo(TodoItem(id="lead-1", text="验证", state=TodoState.IN_PROGRESS, lead_id="L1"))
    assert check_consistency(t, pending_leads=1) == (True, "")


def test_inconsistency_when_lead_pending_but_nothing_in_progress() -> None:
    """有未闭环线索却没在闭环 = 瞎逛。"""
    t = _todo(TodoItem(id="a", text="随便做点什么"))
    ok, why = check_consistency(t, pending_leads=2)
    assert ok is False
    assert "不可见" in why


def test_inconsistency_when_no_lead_but_lead_backed_item_in_progress() -> None:
    """没线索却在忙一件挂着已闭环 lead 的事 = 账本与清单不同步。"""
    t = _todo(TodoItem(id="lead-1", text="验证", state=TodoState.IN_PROGRESS, lead_id="L1"))
    ok, why = check_consistency(t, pending_leads=0)
    assert ok is False
    assert "不同步" in why


def test_plain_todo_item_in_progress_without_leads_is_fine() -> None:
    """没有 lead 背书的普通计划项，在无 pending 线索时 in_progress 是合法的。"""
    t = _todo(TodoItem(id="a", text="整理笔记", state=TodoState.IN_PROGRESS))
    assert check_consistency(t, pending_leads=0) == (True, "")


def test_consistency_both_zero() -> None:
    assert check_consistency(_todo(), pending_leads=0) == (True, "")


# ══════════════════════════════════════════════════════════════════
# 6 统一闸门
# ══════════════════════════════════════════════════════════════════
def test_guard_blocks_write_while_planning() -> None:
    g = _planning()
    with pytest.raises(PlanError, match="计划模式下不允许 fs.write"):
        guard_workbench_op(g, "fs.write", sandbox_mode=GOOD[0], approval_policy=GOOD[1])


def test_guard_passes_after_approval() -> None:
    g = _exited()
    g.approve(by="human")
    guard_workbench_op(g, "fs.write", sandbox_mode=GOOD[0], approval_policy=GOOD[1])


def test_guard_blocks_deleted_ops() -> None:
    with pytest.raises(PlanError, match="不存在"):
        guard_workbench_op(PlanGate(), "fs.delete", sandbox_mode=SandboxMode.FULL,
                           approval_policy=ApprovalPolicy.ALWAYS)


def test_guard_enforces_two_dimensions() -> None:
    with pytest.raises(PlanError, match="sandbox_mode"):
        guard_workbench_op(PlanGate(), "shell.run", sandbox_mode=SandboxMode.READ_ONLY,
                           approval_policy=ApprovalPolicy.ALWAYS)
    with pytest.raises(PlanError, match="approval_policy"):
        guard_workbench_op(PlanGate(), "shell.run", sandbox_mode=SandboxMode.FULL,
                           approval_policy=ApprovalPolicy.NEVER)


def test_guard_blocks_writes_when_todo_unsettled() -> None:
    with pytest.raises(PlanError, match="todo 未收敛"):
        guard_workbench_op(PlanGate(), "fs.write", sandbox_mode=GOOD[0],
                           approval_policy=GOOD[1], todo_locked=True)


def test_guard_allows_reads_when_todo_unsettled() -> None:
    guard_workbench_op(PlanGate(), "fs.read", sandbox_mode=SandboxMode.READ_ONLY,
                       approval_policy=ApprovalPolicy.NEVER, todo_locked=True)


def test_plan_is_a_state_machine_not_a_prompt_switch() -> None:
    """提示词开关靠模型自觉；这里是 guard 能硬拒的状态。"""
    g = PlanGate()
    assert g.unlocked is True
    g.enter(reason="先看清楚")
    assert g.is_planning is True
    with pytest.raises(PlanError):
        g.check("shell.run")
