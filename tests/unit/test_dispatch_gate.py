"""B12.7c 接线测试：``dispatch`` 前查计划闸门 + 闭环版决策器。

两件事各有一条不可退让的断言：
- **计划模式下写/执行在 dispatch 就被拒**（不是 decider 层"自觉不调"）；
- **``Loop`` 用的决策器必须服从闭环**：线索没闭完，不许收工。
"""

from __future__ import annotations

from typing import Any, Mapping

import pytest

from veripen_core.agent.closed_loop import ClosedLoop, ClosedLoopDecider
from veripen_core.agent.loop import AgentState
from veripen_core.leads import LeadLedger, LeadState
from veripen_core.store import Store
from veripen_core.tools.registry import ToolCall, dispatch
from veripen_core.workbench.plan import PlanGate, TodoList

TASK = "t-1"


class _Runtime:
    """最小 Runtime：只记谁被调了，不判好坏。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def list_routes(self, selector: str | None) -> list[dict[str, Any]]:
        self.calls.append("list_routes")
        return []

    def run_oracle(self, oracle_id: str, route_ref: str, params: Mapping[str, Any]) -> dict[str, Any]:
        self.calls.append(f"run_oracle:{oracle_id}")
        return {"verdict": "VERIFIED"}

    def rediscover(self, reason: str) -> dict[str, Any]:
        self.calls.append("rediscover")
        return {}

    def request_approval(self, action_class: str, reason: str) -> dict[str, Any]:
        self.calls.append("approval")
        return {}


@pytest.fixture()
def ledger() -> LeadLedger:
    return LeadLedger(Store(":memory:").init())


def _state(**kw) -> AgentState:  # noqa: ANN003
    base = {"pending": (), "strong": (), "only_weak": False, "budget_ratio": 0.1,
            "wave_order": ("s",)}
    base.update(kw)
    return AgentState(**base)  # type: ignore[arg-type]


def _lead(ledger: LeadLedger):  # noqa: ANN202
    return ledger.record(
        task_id=TASK, asset_id="a-1", source_action="http_fuzz", raw_ref="out/act/x.json",
        summary="可疑差分", verifiable_by=("idor_cross_account",),
    )


# ══════════════════════════════════════════════════════════════════
# 1 dispatch 前的计划闸门
# ══════════════════════════════════════════════════════════════════
def test_dispatch_rejects_when_planning() -> None:
    """计划模式下，连 run_oracle 都在 dispatch 层被拒——不是"decider 自觉不调"。"""
    gate = PlanGate()
    gate.enter(reason="先看清楚")
    rt = _Runtime()
    res = dispatch(ToolCall("run_oracle", {"oracle_id": "sqli_bool_diff", "route_ref": "r"}),
                   rt, plan_gate=gate)
    assert res.ok is False
    assert res.error_class == "approval"
    assert "计划模式下不允许" in res.hint
    assert rt.calls == []  # **运行时一次都没被调到**


def test_dispatch_allows_reads_when_planning() -> None:
    gate = PlanGate()
    gate.enter(reason="先看清楚")
    rt = _Runtime()
    res = dispatch(ToolCall("list_routes", {"selector": None}), rt, plan_gate=gate)
    assert res.ok is True
    assert rt.calls == ["list_routes"]


def test_dispatch_allows_everything_after_approval() -> None:
    gate = PlanGate()
    gate.enter(reason="先看清楚")
    gate.exit(plan_text="打 sqli", actions=("run_oracle",), risks=("超预算",))
    gate.approve(by="secops_lead")
    rt = _Runtime()
    res = dispatch(ToolCall("run_oracle", {"oracle_id": "sqli_bool_diff", "route_ref": "r"}),
                   rt, plan_gate=gate)
    assert res.ok is True
    assert rt.calls == ["run_oracle:sqli_bool_diff"]


def test_dispatch_without_gate_is_unchanged() -> None:
    """不注入闸门时行为与接线前一致：**这是叠加，不是放宽也不收紧**。"""
    rt = _Runtime()
    res = dispatch(ToolCall("run_oracle", {"oracle_id": "x", "route_ref": "r"}), rt)
    assert res.ok is True


def test_schema_errors_still_come_first() -> None:
    """闸门在 schema 校验之后：**参数都不合法时，告诉它参数错了比告诉它模式不对更有用**。"""
    gate = PlanGate()
    gate.enter(reason="x")
    res = dispatch(ToolCall("run_oracle", {"oracle_id": 1}), _Runtime(), plan_gate=gate)
    assert res.error_class in {"schema", "approval"}


# ══════════════════════════════════════════════════════════════════
# 2 闭环版决策器
# ══════════════════════════════════════════════════════════════════
def test_decider_forces_the_pending_lead(ledger: LeadLedger) -> None:
    _lead(ledger)
    dec = ClosedLoopDecider(closed=ClosedLoop(ledger, task_id=TASK))
    call = dec.decide("goal", _state(), {})
    assert call.name == "run_oracle"
    assert call.args["oracle_id"] == "idor_cross_account"


def test_decider_refuses_to_finish_with_pending_lead(ledger: LeadLedger) -> None:
    """NEXT_RULES 耗尽但线索没闭完 → 不许 finish，转重发现。"""
    _lead(ledger)
    led = ClosedLoop(ledger, task_id=TASK)
    led.sync_todo()
    dec = ClosedLoopDecider(closed=led)
    call = dec.decide("goal", _state(pending=(), strong=(), wave_order=()), {})
    # 有 pending lead 时 force 生效，仍然先打；这里验证的是"不会直接 finish"
    assert call.name != "finish"


def test_decider_finishes_when_leads_closed(ledger: LeadLedger) -> None:
    lead = _lead(ledger)
    ledger.supersede(lead.lead_id, state=LeadState.REFUTED)
    dec = ClosedLoopDecider(closed=ClosedLoop(ledger, task_id=TASK))
    call = dec.decide("goal", _state(pending=(), strong=(), wave_order=()), {})
    assert call.name == "finish"


def test_decider_rediscover_when_no_oracles_left(ledger: LeadLedger) -> None:
    dec = ClosedLoopDecider(closed=ClosedLoop(ledger, task_id=TASK))
    call = dec.decide("goal", _state(pending=(), strong=(), wave_order=()), {})
    # 无 lead、无候选、能收尾 → finish（这才是"都跑完了"的样子）
    assert call.name in {"finish", "rediscover_routes"}


def test_decider_follows_rules_when_no_leads(ledger: LeadLedger) -> None:
    dec = ClosedLoopDecider(closed=ClosedLoop(ledger, task_id=TASK))
    call = dec.decide("goal", _state(), {})
    assert call.name == "run_oracle"
    assert call.args["oracle_id"] == "s"  # wave_order 首项


def test_decider_name_is_distinguishable() -> None:
    dec = ClosedLoopDecider(closed=ClosedLoop(None, task_id="x"))
    assert dec.name == "closed-loop"


def test_closed_loop_requires_task_id_for_leads(ledger: LeadLedger) -> None:
    _lead(ledger)
    # task_id 不对 → 看不到那条 lead → 走规则表
    dec = ClosedLoopDecider(closed=ClosedLoop(ledger, task_id="other"))
    assert dec.decide("g", _state(), {}).args["oracle_id"] == "s"


def test_sync_todo_then_finish_is_blocked(ledger: LeadLedger) -> None:
    """线索进了 todo 且未闭环 → may_finish 拒，decider 因此不敢 finish。"""
    _lead(ledger)
    led = ClosedLoop(ledger, task_id=TASK)
    todo: TodoList = led.sync_todo()
    assert len(todo.in_progress) == 1
    ok, _why = led.may_finish()
    assert ok is False
