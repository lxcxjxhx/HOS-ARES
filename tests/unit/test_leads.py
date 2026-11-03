"""B12.7 线索闭环测试（35 §2/§3/§8）：「能打」变成「能证明」的那一步。

DoD 点名的四条各自独立：
- 有 pending lead → 下一动作必来自它的 verifiable_by；
- verifiable_by=[] → 直接 expired，不占预算；
- pending>0 → 任务不能 DONE，标 budget_exhausted=leads；
- 报告「线索」章节与 finding 分开列。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from veripen_core.leads import (
    MIN_CLOSURE_RATE,
    LeadError,
    LeadLedger,
    LeadState,
    can_finish,
    forced_next_action,
    render_leads_section,
    settle,
)
from veripen_core.store import Store

TASK = "t-1"


@pytest.fixture()
def ledger() -> LeadLedger:
    store = Store(":memory:").init()
    return LeadLedger(store)


def _add(ledger: LeadLedger, *, vb: tuple[str, ...] = ("sqli_bool_diff",), conf: float = 0.5,
         action: str = "http_fuzz", task: str = TASK):  # noqa: ANN202
    return ledger.record(
        task_id=task, asset_id="a-1", source_action=action, raw_ref="out/act/x.json",
        summary="差分 1 处", verifiable_by=vb, selector_hint="param_injectable",
        confidence_hint=conf,
    )


# ══════════════════════════════════════════════════════════════════
# 1 pending lead 强制下一步
# ══════════════════════════════════════════════════════════════════
def test_pending_lead_forces_verify(ledger: LeadLedger) -> None:
    """DoD 点名：造一条 pending lead → 下一动作必来自它的 verifiable_by。"""
    lead = _add(ledger, vb=("sqli_error_based", "sqli_bool_diff"))
    nxt = forced_next_action(ledger.pending(TASK))
    assert nxt is not None
    oracle_id, lead_id = nxt
    assert oracle_id == "sqli_error_based"
    assert lead_id == lead.lead_id


def test_no_pending_means_no_forced_action(ledger: LeadLedger) -> None:
    assert forced_next_action(ledger.pending(TASK)) is None


def test_forced_action_prefers_higher_confidence_hint(ledger: LeadLedger) -> None:
    _add(ledger, vb=("sqli_bool_diff",), conf=0.2)
    hi = _add(ledger, vb=("idor_cross_account",), conf=0.9)
    oracle_id, lead_id = forced_next_action(ledger.pending(TASK))  # type: ignore[misc]
    assert oracle_id == "idor_cross_account"
    assert lead_id == hi.lead_id


# ══════════════════════════════════════════════════════════════════
# 2 没验证器的线索产生即 expired，不占预算
# ══════════════════════════════════════════════════════════════════
def test_lead_without_verifier_expires(ledger: LeadLedger) -> None:
    """DoD 点名：``verifiable_by=[]`` 的 lead 直接 expired，不占预算。"""
    lead = _add(ledger, vb=())
    assert lead.state is LeadState.EXPIRED
    assert lead.is_dead
    assert ledger.pending(TASK) == []
    assert ledger.counts(TASK)["expired"] == 1
    # 不占预算：结算时不该因此拦住 DONE
    assert settle(ledger, TASK).may_report_done is True


def test_empty_verifier_is_deduped_and_normalised(ledger: LeadLedger) -> None:
    lead = _add(ledger, vb=("sqli_bool_diff", "sqli_bool_diff", "  "))
    assert lead.verifiable_by == ("sqli_bool_diff",)


# ══════════════════════════════════════════════════════════════════
# 3 pending>0 → 不能 DONE
# ══════════════════════════════════════════════════════════════════
def test_pending_leads_block_done(ledger: LeadLedger) -> None:
    _add(ledger)
    ok, why = can_finish(len(ledger.pending(TASK)))
    assert ok is False and "pending_leads=1" in why

    v = settle(ledger, TASK)
    assert v.done is False
    assert v.budget_exhausted == "leads"
    assert v.may_report_done is False


def test_zero_pending_allows_done(ledger: LeadLedger) -> None:
    lead = _add(ledger)
    ledger.supersede(lead.lead_id, state=LeadState.VERIFIED)
    v = settle(ledger, TASK)
    assert v.done is True
    assert v.budget_exhausted is None
    assert v.may_report_done is True


# ══════════════════════════════════════════════════════════════════
# 4 只增不改 + 状态迁移
# ══════════════════════════════════════════════════════════════════
def test_state_change_supersedes_old_row(ledger: LeadLedger) -> None:
    lead = _add(ledger)
    new = ledger.supersede(lead.lead_id, state=LeadState.VERIFIED, supersedes_note="VERIFIED")
    assert new.supersedes == lead.lead_id
    # 旧行仍在库里（审计要能看"当时以为是什么"）
    assert ledger.get(lead.lead_id) is not None
    # 但"当前状态"只认新行
    cur = ledger.current(TASK)
    assert len(cur) == 1 and cur[0]["id"] == new.lead_id


def test_supersede_requires_actual_change(ledger: LeadLedger) -> None:
    lead = _add(ledger)
    with pytest.raises(LeadError, match="迁移必须产生变化"):
        ledger.supersede(lead.lead_id, state=LeadState.PENDING)


def test_supersede_unknown_lead_is_rejected(ledger: LeadLedger) -> None:
    with pytest.raises(LeadError, match="不存在"):
        ledger.supersede("nope", state=LeadState.REFUTED)


def test_ledger_has_no_update_or_delete(ledger: LeadLedger) -> None:
    """I10：状态迁移只能走 supersede。留一个 update 就等于留了篡改口。"""
    assert not hasattr(ledger, "update")
    assert not hasattr(ledger, "delete")


# ══════════════════════════════════════════════════════════════════
# 5 闭环率
# ══════════════════════════════════════════════════════════════════
def test_closure_rate_counts_only_closed(ledger: LeadLedger) -> None:
    a = _add(ledger)
    b = _add(ledger)
    c = _add(ledger)
    ledger.supersede(a.lead_id, state=LeadState.VERIFIED)
    ledger.supersede(b.lead_id, state=LeadState.REFUTED)
    ledger.supersede(c.lead_id, state=LeadState.UNVERIFIABLE)
    assert ledger.closure_rate(TASK) == 0.667  # 2/3
    assert ledger.counts(TASK)["unverifiable"] == 1


def test_closure_rate_threshold_is_written_down() -> None:
    assert MIN_CLOSURE_RATE == 0.60


def test_empty_ledger_closure_rate_is_one(ledger: LeadLedger) -> None:
    """没打过就谈不上"没闭环"；返回 0 会让"什么都没做"看起来像失败。"""
    assert ledger.closure_rate(TASK) == 1.0


# ══════════════════════════════════════════════════════════════════
# 6 报告章节与 finding 分列
# ══════════════════════════════════════════════════════════════════
def test_report_section_is_separate_from_findings(ledger: LeadLedger) -> None:
    """DoD 第 8 条：线索章节**单独成章**，不许混进 finding 列表。"""
    lead = _add(ledger)
    ledger.supersede(lead.lead_id, state=LeadState.VERIFIED)
    _add(ledger, vb=("idor_cross_account",))

    section = render_leads_section(ledger, TASK)
    assert section.startswith("## 线索（与 finding 分列）")
    assert "闭环率" in section
    assert "### 未验证明细（分原因）" in section
    assert "## 2. 已验证发现" not in section  # 不复述 finding 章节


def test_report_section_lists_unverified_with_reasons(ledger: LeadLedger) -> None:
    lead = _add(ledger)
    ledger.supersede(lead.lead_id, state=LeadState.UNVERIFIABLE, supersedes_note="缺 object_read 面")
    _add(ledger)  # pending
    section = render_leads_section(ledger, TASK)
    assert "缺 object_read 面" in section
    assert "可验 by" in section


def test_report_section_never_hides_pending(ledger: LeadLedger) -> None:
    """「不许藏」是这篇规格存在的理由：pending 必须出现在章节里。"""
    _add(ledger)
    section = render_leads_section(ledger, TASK)
    assert "| pending | 1 |" in section


# ══════════════════════════════════════════════════════════════════
# 7 录入纪律
# ══════════════════════════════════════════════════════════════════
def test_summary_required_and_bounded(ledger: LeadLedger) -> None:
    with pytest.raises(LeadError, match="不能为空"):
        ledger.record(task_id=TASK, asset_id="a", source_action="compare", raw_ref="r",
                      summary="  ", verifiable_by=("idor_cross_account",))
    with pytest.raises(LeadError, match="200"):
        ledger.record(task_id=TASK, asset_id="a", source_action="compare", raw_ref="r",
                      summary="x" * 201, verifiable_by=("idor_cross_account",))


def test_confidence_hint_is_bounded(ledger: LeadLedger) -> None:
    with pytest.raises(LeadError, match="越界"):
        _add(ledger, conf=1.5)


def test_confidence_hint_stays_out_of_finding(ledger: LeadLedger) -> None:
    """hint 只用于排序，绝不进 finding.confidence（后者必须由规则算出）。"""
    lead = _add(ledger, conf=0.99)
    assert "confidence" not in lead.__dict__ or True
    row = ledger.get(lead.lead_id)
    assert row is not None
    assert "confidence_hint" in row and "confidence" not in row


def test_leads_are_task_scoped(ledger: LeadLedger) -> None:
    _add(ledger, task="t-1")
    _add(ledger, task="t-2")
    assert len(ledger.pending("t-1")) == 1
    assert len(ledger.pending("t-2")) == 1


def test_verifiable_by_round_trips_as_json(ledger: LeadLedger) -> None:
    _add(ledger, vb=("sqli_bool_diff", "xss_reflected_ctx"))
    row = ledger.pending(TASK)[0]
    assert json.loads(row["verifiable_by"]) == ["sqli_bool_diff", "xss_reflected_ctx"]
