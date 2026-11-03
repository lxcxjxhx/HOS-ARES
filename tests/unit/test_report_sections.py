"""B12.7+B13 报告接线测试：线索与死信**单列**（09 §3）。

两段都是"已经有渲染函数、但没人调用"。这批把它们接进三视图。

三条纪律：
- 线索**与 finding 分开**：它是"打过但还没证完的"，证据强度不同；
- 死信**单列**：它既不是"未发现"也不是"error"，是"重试用完了"；
- 两段都**可选**：没有线索/没有死信的报告不该多出两个空标题。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from veripen.report.render import (
    FindingView,
    Outcome,
    ReportInput,
    render_json,
    render_markdown,
    render_sarif,
)
from veripen_core.deadletter import DeadLetterError, render_section as dead_section
from veripen_core.deadletter import to_dead_letter
from veripen_core.leads import LeadLedger, LeadState, render_leads_section
from veripen_core.models import TaskState
from veripen_core.store import Store

REF = "route://observed/" + "a" * 16


def _view(fid: str = "f1", **kw) -> FindingView:  # noqa: ANN003
    base = dict(
        finding_id=fid, title="反射型 XSS", asset_id="a-1", oracle_id="xss_reflected_ctx",
        cwe="CWE-79", severity="medium", confidence=0.9, route_strength="observed",
        route_ref=REF, capsule_id="sha256:" + "b" * 64, self_sha256="c" * 64,
        replay_status="OK", outcome=Outcome.REPORTED,
    )
    base.update(kw)
    return FindingView(**base)  # type: ignore[arg-type]


def _report(**kw) -> ReportInput:  # noqa: ANN003
    base = dict(engagement="e", profile_hash="p" * 64, image_digest="i" * 64,
                root_sha256="r" * 64, findings=[_view()])
    base.update(kw)
    return ReportInput(**base)  # type: ignore[arg-type]


@pytest.fixture()
def ledger() -> LeadLedger:
    return LeadLedger(Store(":memory:").init())


# ══════════════════════════════════════════════════════════════════
# 1 线索章节
# ══════════════════════════════════════════════════════════════════
def test_leads_section_appears_in_markdown(ledger: LeadLedger) -> None:
    lead = ledger.record(task_id="t", asset_id="a-1", source_action="http_fuzz",
                         raw_ref="out/act/x.json", summary="差分 1 处",
                         verifiable_by=("sqli_bool_diff",))
    md = render_markdown(_report(leads_section=render_leads_section(ledger, "t")))
    assert "## 线索（与 finding 分列）" in md
    assert "闭环率" in md
    assert "| pending | 1 |" in md


def test_leads_section_is_separate_from_findings(ledger: LeadLedger) -> None:
    """线索章节在 finding 章节之后，且不复述 finding。"""
    md = render_markdown(_report(leads_section=render_leads_section(ledger, "t")))
    i_find = md.find("## 2. 已验证发现")
    i_leads = md.find("## 线索（与 finding 分列）")
    assert i_find > 0 and i_leads > i_find


def test_leads_section_absent_when_not_given() -> None:
    md = render_markdown(_report())
    assert "## 线索" not in md


def test_leads_section_is_reported_in_json(ledger: LeadLedger) -> None:
    sec = render_leads_section(ledger, "t")
    js = render_json(_report(leads_section=sec))
    assert js["leads_section"] == sec
    # 线索**不混进 findings 数组**
    assert all("lead" not in str(f.get("id", "")) for f in js["findings"])


# ══════════════════════════════════════════════════════════════════
# 2 死信章节
# ══════════════════════════════════════════════════════════════════
def _dead_store() -> Store:
    store = Store(":memory:").init()
    tid, _ = store.submit_task(engagement_id="e", profile_name="p", profile_hash="h",
                               asset_id="a", oracle_ids=["o"], max_attempts=3)
    store.conn.execute("UPDATE tasks SET state=?, attempt=3 WHERE id=?",
                       (TaskState.FAILED.value, tid))
    store.conn.commit()
    to_dead_letter(store, tid, error_class="retryable", error_code="VP-7001")
    return store


def test_dead_section_appears_in_markdown() -> None:
    store = _dead_store()
    md = render_markdown(_report(dead_section=dead_section(store)))
    assert "## 死信（单列）" in md
    assert "不计入 error 率" in md


def test_dead_section_absent_when_not_given() -> None:
    assert "## 死信" not in render_markdown(_report())


def test_dead_section_is_reported_in_json() -> None:
    store = _dead_store()
    sec = dead_section(store)
    js = render_json(_report(dead_section=sec))
    assert js["dead_section"] == sec


def test_dead_and_leads_coexist_in_order(ledger: LeadLedger) -> None:
    """两段可以同时存在，且顺序是：覆盖 → 线索 → 死信 → 合规 → 附录。"""
    md = render_markdown(_report(
        leads_section=render_leads_section(ledger, "t"),
        dead_section=dead_section(_dead_store()),
    ))
    i_cov = md.find("## 4. 覆盖范围与未覆盖")
    i_leads = md.find("## 线索（与 finding 分列）")
    i_dead = md.find("## 死信（单列）")
    i_app = md.find("## 6. 附录")
    assert i_cov < i_leads < i_dead < i_app


# ══════════════════════════════════════════════════════════════════
# 3 零地址与其他纪律不被破坏
# ══════════════════════════════════════════════════════════════════
def test_sections_do_not_break_zero_address_rule(ledger: LeadLedger) -> None:
    """线索与死信章节都来自库，库里不该有地址；渲染后仍必须零地址。"""
    from veripen.report.lint import lint_text

    md = render_markdown(_report(
        leads_section=render_leads_section(ledger, "t"),
        dead_section=dead_section(_dead_store()),
    ))
    assert lint_text(md, where="markdown") == []


def test_sarif_still_renders_with_sections(ledger: LeadLedger) -> None:
    """SARIF 只装 findings；线索/死信不塞进 results。"""
    sarif = render_sarif(_report(leads_section=render_leads_section(ledger, "t")))
    assert len(sarif["runs"][0]["results"]) == 1


def test_empty_sections_do_not_add_headings(ledger: LeadLedger) -> None:
    md = render_markdown(_report(leads_section="", dead_section="   "))
    assert "## 线索" not in md and "## 死信" not in md
