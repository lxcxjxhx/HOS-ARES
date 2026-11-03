"""B12.2 · outcome 四态 + 覆盖来源（32 §3 A1/A2、09 §3、10 §3.0）。

这批的核心不是"多两列"，而是**跑挂了不许冒充清白**：
- error / not_run 三处联动拒绝（模型 → 落库 → 库触发器）；
- agent_reported 强制降级 not_run；
- 报告三视图四态计数来自库，不从模型自述。
"""

from __future__ import annotations

import sqlite3

import pytest

from veripen.report import lint as report_lint
from veripen.report.render import (
    FindingView,
    Outcome,
    ReportInput,
    counts_by_outcome,
    render_markdown,
    render_sarif,
    render_json,
)
from veripen_core import outcome as oc
from veripen_core.models import CoverageSrc, Finding, FindingState
from veripen_core.store import Store

BASE = dict(
    id="f-1",
    task_id="t-1",
    asset_id="a-1",
    oracle_id="xss-reflected",
    oracle_version="1.0.0",
    title="反射型 XSS 可复现",
    severity="medium",
    confidence=0.9,
    cwe="CWE-79",
    route_strength="observed",
    capsule_id="sha256:" + "a" * 64,
)


def _store() -> Store:
    return Store(":memory:").init()


def _finding(**kw: object) -> Finding:
    data = dict(BASE)
    data.update(kw)
    return Finding(**data)  # type: ignore[arg-type]


# ══════════════════════════════════════════════════════════════════
# 1. 三条自洽规则（纯函数层）
# ══════════════════════════════════════════════════════════════════
def test_error_requires_blocked_state() -> None:
    with pytest.raises(oc.OutcomeViolation, match="BLOCKED"):
        oc.validate(
            outcome="error", state=FindingState.VERIFIED, coverage_src="machine_observed",
            error_reason="timeout", not_run_reason=None,
        )


def test_not_run_requires_blocked_state() -> None:
    with pytest.raises(oc.OutcomeViolation, match="BLOCKED"):
        oc.validate(
            outcome="not_run", state=FindingState.UNVERIFIABLE, coverage_src="machine_observed",
            error_reason=None, not_run_reason="skipped_scope",
        )


def test_agent_reported_forces_not_run() -> None:
    """自述冒充"已覆盖"在 schema / 库里都不可表达——这是 A2 的核心。"""
    with pytest.raises(oc.OutcomeViolation, match="agent_reported"):
        oc.validate(
            outcome="reported", state=FindingState.VERIFIED, coverage_src="agent_reported",
            error_reason=None, not_run_reason=None,
        )


def test_reason_codes_are_required_and_exclusive() -> None:
    with pytest.raises(oc.OutcomeViolation, match="error_reason"):
        oc.validate(
            outcome="error", state=FindingState.BLOCKED, coverage_src="none",
            error_reason=None, not_run_reason=None,
        )
    with pytest.raises(oc.OutcomeViolation, match="not_run_reason"):
        oc.validate(
            outcome="not_run", state=FindingState.BLOCKED, coverage_src="none",
            error_reason=None, not_run_reason="because",
        )
    with pytest.raises(oc.OutcomeViolation, match="不得带 error_reason"):
        oc.validate(
            outcome="reported", state=FindingState.VERIFIED, coverage_src="machine_observed",
            error_reason="timeout", not_run_reason=None,
        )


def test_legal_states_pass() -> None:
    assert oc.validate(
        outcome="reported", state=FindingState.VERIFIED, coverage_src="machine_observed",
        error_reason=None, not_run_reason=None,
    ) is oc.Outcome.REPORTED
    assert oc.validate(
        outcome="not_run", state=FindingState.BLOCKED, coverage_src="agent_reported",
        error_reason=None, not_run_reason="approval_pending",
    ) is oc.Outcome.NOT_RUN


# ══════════════════════════════════════════════════════════════════
# 2. 模型层：pydantic 在构造时就炸
# ══════════════════════════════════════════════════════════════════
def test_model_rejects_error_with_verified_state() -> None:
    with pytest.raises(ValueError, match="BLOCKED"):
        _finding(outcome="error", error_reason="timeout", state=FindingState.VERIFIED)


def test_model_accepts_legal_error() -> None:
    f = _finding(outcome="error", error_reason="transport", state=FindingState.BLOCKED)
    assert f.outcome.value == "error"


# ══════════════════════════════════════════════════════════════════
# 3. 库触发器：裸 SQL 也绕不过去
# ══════════════════════════════════════════════════════════════════
_RAW = (
    "INSERT INTO findings (id, task_id, asset_id, oracle_id, oracle_version, title, severity,"
    " confidence, cwe, state, created_at, outcome, coverage_src, not_run_reason, error_reason)"
    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)


def _raw_row(fid: str, **kw: object) -> tuple[object, ...]:
    row: list[object] = [fid, "t-1", "a-1", "x", "1.0.0", "标题四个字", "low", 0.5, "CWE-79",
                         "VERIFIED", 0, "reported", "machine_observed", None, None]
    idx = {"state": 9, "outcome": 11, "coverage_src": 12, "not_run_reason": 13, "error_reason": 14}
    for key, val in kw.items():
        row[idx[key]] = val
    return tuple(row)


def _raw(store: Store, **kw: object) -> None:
    store.conn.execute(_RAW, _raw_row("r-1", **kw))


def test_trigger_blocks_error_counted_as_conclusion() -> None:
    """这条就是 DoD 点名的"error 计成 TN 的路径有一条测试会红"。"""
    store = _store()
    with pytest.raises(sqlite3.IntegrityError, match="VP-8002"):
        _raw(store, outcome="error", error_reason="timeout")
    with pytest.raises(sqlite3.IntegrityError, match="VP-8002"):
        _raw(store, outcome="not_run", not_run_reason="skipped_budget")


def test_trigger_blocks_agent_reported_coverage() -> None:
    store = _store()
    with pytest.raises(sqlite3.IntegrityError, match="agent_reported"):
        _raw(store, coverage_src="agent_reported")


def test_trigger_update_path_is_guarded_too() -> None:
    """老库 ALTER 出来的行，改判也要过闸——只守 INSERT 等于没守。"""
    store = _store()
    _raw(store)  # 合法行
    with pytest.raises(sqlite3.IntegrityError, match="VP-8002"):
        store.conn.execute(
            "UPDATE findings SET outcome='error', error_reason='timeout' WHERE id='r-1'"
        )
    store.conn.execute(
        "UPDATE findings SET outcome='not_run', state='BLOCKED', not_run_reason='skipped_scope'"
        " WHERE id='r-1'"
    )
    assert store.counts_by_outcome()["not_run"] == 1


# ══════════════════════════════════════════════════════════════════
# 4. 落库 + 计数
# ══════════════════════════════════════════════════════════════════
def test_record_finding_roundtrip_four_states() -> None:
    store = _store()
    # findings 有 (task, oracle, asset) 唯一约束：四态各用一组，别让约束替 outcome 背锅
    store.record_finding(_finding(id="f-ok"))
    store.record_finding(_finding(
        id="f-und", task_id="t-2", outcome="undetermined", state=FindingState.UNVERIFIABLE,
        capsule_id=None))
    store.record_finding(_finding(
        id="f-err", task_id="t-3", outcome="error", state=FindingState.BLOCKED, capsule_id=None,
        error_reason="target_down"))
    store.record_finding(_finding(
        id="f-skip", task_id="t-4", outcome="not_run", state=FindingState.BLOCKED, capsule_id=None,
        coverage_src=CoverageSrc.AGENT_REPORTED, not_run_reason="skipped_scope"))

    assert store.counts_by_outcome() == {
        "reported": 1, "undetermined": 1, "error": 1, "not_run": 1,
    }
    assert store.agent_reported_count() == 1
    assert store.get_finding("f-err")["error_reason"] == "target_down"


def test_record_finding_refuses_inconsistent_before_sql() -> None:
    store = _store()
    bad = _finding(outcome="reported", state=FindingState.VERIFIED)
    bad = bad.model_copy(update={"coverage_src": CoverageSrc.AGENT_REPORTED})
    with pytest.raises(oc.OutcomeViolation, match="agent_reported"):
        store.record_finding(bad)


def test_counts_treat_unknown_value_as_error() -> None:
    """列被手改成怪值：记 error，**绝不并进 reported**。"""
    assert oc.counts([{"outcome": "reported"}, {"outcome": "banana"}, {}]) == {
        "reported": 1, "undetermined": 0, "error": 2, "not_run": 0,
    }


# ══════════════════════════════════════════════════════════════════
# 5. 报告三视图：四态计数同源
# ══════════════════════════════════════════════════════════════════
def _view(fid: str, outcome: Outcome, **kw: object) -> FindingView:
    base: dict[str, object] = dict(
        finding_id=fid, title="t", asset_id="a-1", oracle_id="xss-reflected", cwe="CWE-79",
        severity="medium", confidence=0.9, route_strength="observed",
        route_ref="route://observed/0123456789abcdef", capsule_id="sha256:" + "b" * 64,
        self_sha256="c" * 64, replay_status="OK", outcome=outcome,
    )
    base.update(kw)
    return FindingView(**base)  # type: ignore[arg-type]


def test_three_views_share_the_same_four_state_counts() -> None:
    views = [
        _view("f1", Outcome.REPORTED),
        _view("f2", Outcome.UNDETERMINED, capsule_id=None, self_sha256=None),
        _view("f3", Outcome.ERROR, capsule_id=None, self_sha256=None, reason_code="transport"),
        _view("f4", Outcome.NOT_RUN, capsule_id=None, self_sha256=None, reason_code="skipped_scope"),
    ]
    report = ReportInput(
        engagement="e", profile_hash="p" * 64, image_digest="i" * 64, root_sha256="r" * 64,
        findings=views,
        outcome_counts={"reported": 1, "undetermined": 1, "error": 1, "not_run": 1},
    )
    js = render_json(report)
    sarif = render_sarif(report)
    md = render_markdown(report)

    assert js["counts"]["by_outcome"] == {"reported": 1, "undetermined": 1, "error": 1, "not_run": 1}
    assert sarif["runs"][0]["properties"]["outcomes"] == js["counts"]["by_outcome"]
    for label in ("reported", "undetermined", "error", "not_run"):
        assert f"| {label} | 1 |" in md
    assert report_lint.lint_text(md, where="markdown") == []


def test_store_counts_override_row_views() -> None:
    """库层给了计数就以库为准：视图漏一条也藏不住跑挂。"""
    report = ReportInput(
        engagement="e", profile_hash="p" * 64, image_digest="i" * 64, root_sha256="r" * 64,
        findings=[_view("f1", Outcome.REPORTED)],
        outcome_counts={"error": 2},
    )
    assert counts_by_outcome(report)["error"] == 2
    assert counts_by_outcome(report)["reported"] == 0


def test_markdown_never_puts_error_near_safe_wording() -> None:
    """09 §3 铁律：error / not_run 不得出现在「未发现/安全」表述附近。"""
    md = render_markdown(ReportInput(
        engagement="e", profile_hash="p" * 64, image_digest="i" * 64, root_sha256="r" * 64,
        findings=[_view("f3", Outcome.ERROR, capsule_id=None, self_sha256=None,
                        reason_code="transport")],
    ))
    section3 = md.split("## 3.")[1]
    assert "执行失败" in section3
    assert "未发现" not in section3 and "安全" not in section3


def test_outcome_does_not_count_when_error_or_not_run() -> None:
    assert not oc.Outcome.ERROR.counts_for_metrics
    assert not oc.Outcome.NOT_RUN.counts_for_metrics
    assert not oc.Outcome.NOT_RUN.is_covered
    assert oc.Outcome.REPORTED.counts_for_metrics
