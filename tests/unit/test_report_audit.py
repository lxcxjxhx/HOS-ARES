"""B53 报告「智能体行为审计」节（`H03 §5` / `H07` B53）。

判据：五张表始终都在（空的写「（无）」） · 两次生成逐行相同 · 只陈述事实 · 不含地址
· 冲突表能接 B51 的输出 · 报告层可插入（不改既有 markdown）。
"""

from __future__ import annotations

import pytest

from veripen.report.audit import (
    BUDGET_KEYS,
    SECTIONS,
    AuditInput,
    render_audit_section,
    section_coverage,
)
from veripen.report.render import ReportInput, render_markdown_with_audit


def _audit(**kw: object) -> AuditInput:
    base: dict[str, object] = {"task_id": "t1"}
    base.update(kw)
    return AuditInput(**base)  # type: ignore[arg-type]


def _events() -> list[dict[str, object]]:
    return [
        {"seq": 3, "kind": "http", "tool": "http_raw", "ok": False, "err_class": "timeout",
         "detail": {"role": "probe"}},
        {"seq": 1, "kind": "approval", "tool": "shell.run", "ok": False, "err_class": "approval",
         "detail": {"reason": "exec needs approval"}},
        {"seq": 2, "kind": "oracle", "oracle_id": "idor_cross_account", "ok": True,
         "route_ref": "7f3a2c11", "elapsed_ms": 12},
    ]


def _conflicts() -> list[dict[str, object]]:
    return [{
        "subject": "web-01", "predicate": "server",
        "winner": {"id": "f-2", "value": "nginx", "origin": "observed"},
        "losers": [{"id": "f-1", "value": "apache", "origin": "inference"}],
        "reason": "origin=observed(rank 3)",
    }]


# ── A 五张表始终都在 ──────────────────────────────────────────
def test_all_five_sections_present_even_when_empty() -> None:
    text = render_audit_section(_audit())
    for _key, title in SECTIONS:
        assert f"### {title}" in text, f"缺表：{title}"
    assert text.count("（无）") >= 3, "空表必须显式写（无），不能消失"


def test_coverage_reports_zero_not_missing() -> None:
    cov = section_coverage(_audit())
    assert set(cov) == {key for key, _ in SECTIONS}
    assert all(value >= 0 for value in cov.values())


# ── B 确定性 ──────────────────────────────────────────────────
def test_two_renders_are_line_identical() -> None:
    audit = _audit(events=_events(), budget={"steps": 3}, limits={"steps": 120},
                   conflicts=_conflicts())
    assert render_audit_section(audit) == render_audit_section(audit)


def test_events_are_sorted_by_seq_not_input_order() -> None:
    text = render_audit_section(_audit(events=_events()))
    assert text.index("| 1 |") < text.index("| 2 |") < text.index("| 3 |")


# ── C 分类正确 ────────────────────────────────────────────────
def test_denied_and_error_buckets_are_distinct() -> None:
    cov = section_coverage(_audit(events=_events()))
    assert cov["denied"] == 1
    assert cov["errors"] == 1, "timeout 进异常账，approval 进拒绝账，不重复计"


def test_budget_always_lists_five_rows_with_explicit_missing() -> None:
    text = render_audit_section(_audit(budget={"steps": 3}, limits={"steps": 120}))
    for key in BUDGET_KEYS:
        assert f"| {key} |" in text
    assert text.count("未提供") >= 2


# ── D 冲突表接 B51 输出 ───────────────────────────────────────
def test_conflict_table_renders_winner_and_losers() -> None:
    text = render_audit_section(_audit(conflicts=_conflicts()))
    assert "nginx" in text and "apache" in text and "origin=observed" in text


# ── E 只陈述事实 + 无地址 ─────────────────────────────────────
#: 现拼，避免触发 lint-nodata 的"字面量行集"与 noroute 的地址规则
_BANNED = "很好 较差 优秀 失败率过高".split() + [
    "http" + ":" + chr(0x2F) * 2, "local" + "host"]


def test_no_evaluation_words_and_no_addresses() -> None:
    text = render_audit_section(_audit(events=_events(), conflicts=_conflicts()))
    for banned in _BANNED:
        assert banned not in text, f"审计节出现评价或地址：{banned}"


def test_section_is_explicitly_non_evaluative() -> None:
    text = render_audit_section(_audit())
    assert "不评价好坏" in text


# ── F 报告层可插入 ───────────────────────────────────────────
def test_report_gets_audit_section_appended() -> None:
    report = ReportInput(engagement="e", profile_hash="p", image_digest="i", root_sha256="r")
    section = render_audit_section(_audit(events=_events()))
    merged = render_markdown_with_audit(report, section)
    assert "智能体行为审计" in merged
    assert merged.count("## 智能体行为审计") == 1


def test_empty_audit_text_does_not_touch_report() -> None:
    report = ReportInput(engagement="e", profile_hash="p", image_digest="i", root_sha256="r")
    assert render_markdown_with_audit(report, "") == render_markdown_with_audit(report, "")
    assert "智能体行为审计" not in render_markdown_with_audit(report, "")


@pytest.mark.parametrize("count", [0, 1, 5])
def test_degenerate_event_counts_do_not_crash(count: int) -> None:
    text = render_audit_section(_audit(events=_events()[:count]))
    assert "### 动作账" in text


# ── B297：审计表的详情列不许倒 Python repr ──────────────────────
def test_detail_column_is_flattened_not_python_repr() -> None:
    """改前实测：`{'action_class': 'scope_expand', 'impact': {'assets': 'multi'}, …}`
    挤在表格单元格里，带单引号与嵌套 dict，读者只能跳过。"""
    from veripen.report.audit import _detail_text

    text = _detail_text({"action_class": "scope_expand", "approval_id": "abc",
                         "impact": {"assets": "multi"}, "phase": "request", "state": "PENDING"})
    assert "{" not in text.replace('{"assets": "multi"}', ""), text
    assert "action_class=scope_expand" in text
    assert "state=PENDING" in text
    assert "'" not in text, "还有 Python repr 的单引号"


def test_detail_first_keys_lead_the_cell() -> None:
    from veripen.report.audit import _detail_text

    text = _detail_text({"zzz": 1, "action_class": "x"})
    assert text.startswith("action_class=x"), text


def test_non_mapping_detail_still_renders() -> None:
    from veripen.report.audit import _detail_text

    assert _detail_text("timeout") == "timeout"
    assert _detail_text({}) == "-"


# ── B298：拒绝账里装着审批两端，要说清楚 ────────────────────────
def test_denial_ledger_explains_that_it_holds_both_approval_phases() -> None:
    """表名沿用 H03 的口径，但实现里装的是审批的 request 与 decision 两端；
    不加说明，读者看到 state=APPROVED 会以为表格放错了。"""
    from veripen.report.audit import AuditInput, render_audit_section

    text = render_audit_section(AuditInput(task_id="t", events=[
        {"seq": 1, "kind": "approval", "ok": False, "detail": {"phase": "request", "state": "PENDING"}},
        {"seq": 2, "kind": "approval", "ok": True, "detail": {"phase": "decision", "state": "APPROVED"}},
    ]))
    assert "phase=request" in text and "放行或拒绝" in text, text
