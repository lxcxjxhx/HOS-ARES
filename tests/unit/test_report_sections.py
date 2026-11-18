"""B12.7+B13 报告接线测试：线索与死信**单列**（09 §3）。

两段都是"已经有渲染函数、但没人调用"。这批把它们接进三视图。

三条纪律：
- 线索**与 finding 分开**：它是"打过但还没证完的"，证据强度不同；
- 死信**单列**：它既不是"未发现"也不是"error"，是"重试用完了"；
- 两段都**可选**：没有线索/没有死信的报告不该多出两个空标题。
"""

from __future__ import annotations

from pathlib import Path

import re

import pytest

from hos_ares.report.render import (
    FindingView,
    Outcome,
    ReportInput,
    render_json,
    render_markdown,
    render_sarif,
)
from hos_ares_core.deadletter import DeadLetterError, render_section as dead_section
from hos_ares_core.deadletter import to_dead_letter
from hos_ares_core.leads import LeadLedger, LeadState, render_leads_section
from hos_ares_core.models import TaskState
from hos_ares_core.store import Store

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
    """两段可以同时存在，且顺序是：覆盖 → 线索 → 死信 → 合规（可选）→ 附录。

    附录编号按有没有合规映射决定（B246），所以按 `## N. 附录` 找，不写死数字。
    """
    md = render_markdown(_report(
        leads_section=render_leads_section(ledger, "t"),
        dead_section=dead_section(_dead_store()),
    ))
    i_cov = md.find("## 4. 覆盖范围与未覆盖")
    i_leads = md.find("## 线索（与 finding 分列）")
    i_dead = md.find("## 死信（单列）")
    match = re.search(r"^## \d+\. 附录$", md, re.MULTILINE)
    assert match is not None, "找不到附录标题"
    assert i_cov < i_leads < i_dead < match.start()


# ══════════════════════════════════════════════════════════════════
# 3 零地址与其他纪律不被破坏
# ══════════════════════════════════════════════════════════════════
def test_sections_do_not_break_zero_address_rule(ledger: LeadLedger) -> None:
    """线索与死信章节都来自库，库里不该有地址；渲染后仍必须零地址。"""
    from hos_ares.report.lint import lint_text

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


# ── B368：§3 不许把"已判否"写成"未执行" ────────────────────────
def _finding(fid: str, *, outcome: Outcome, refuted: bool, reason: str) -> FindingView:
    return FindingView(
        finding_id=fid, title="t", asset_id="a", oracle_id="sqli_bool_diff",
        cwe="CWE-89", severity="high", confidence=0.0, route_strength="contract",
        route_ref=REF, capsule_id=None, self_sha256=None, replay_status="NEVER",
        situation="一句话", outcome=outcome, refuted=refuted, reason_code=reason,
    )


def test_unverified_section_does_not_call_refuted_items_not_run() -> None:
    """四态没有"已判否"这一格，REFUTED 并进 NOT_RUN 桶——但**不能照着桶分节**。

    实测（B368）：打一个只会返回垃圾的靶子，12 项里 10 项是"跑过、无该问题"，
    §0 写着「真正未执行 0 条」，§3 却印「未执行（哪一类要写明） · 10」——
    同一份报告上下自相矛盾，读者会得出"没测"的反结论。
    """
    report = ReportInput(
        engagement="E", profile_hash="h", image_digest="d", root_sha256="a" * 64,
        routeset_sha256=["b"], resolver_version="1", render_time="t", distribution="internal",
        findings=[
            _finding("f-refuted", outcome=Outcome.NOT_RUN, refuted=True,
                     reason="no_differential"),
            _finding("f-blocked", outcome=Outcome.NOT_RUN, refuted=False,
                     reason="peer_in_deny"),
        ])
    md = render_markdown(report)
    assert "### 未执行（哪一类要写明） · 1" in md, md
    assert "### 已判否（跑过、无该问题） · 1" in md, md
    assert "未执行（哪一类要写明） · 2" not in md, "已判否不许被算进未执行"


# ── B396：能力边界要写进交付物 ─────────────────────────────────
def test_capability_limits_are_rendered_with_reasons() -> None:
    """「这一轮不可能有结论」的判定，报告里必须点名并写原因。

    以前这些只活在矩阵脚本与账本里：客户拿到报告，看到的是"覆盖 14/14"，
    读不到"哪些东西我们测不了、为什么测不了"。
    """
    from hos_ares.report.render import ReportInput, render_markdown

    report = ReportInput(
        engagement="demo", profile_hash="", image_digest="", root_sha256="",
        capability_limits={
            "needs_sequence": [("S3", "登出后再用旧会话：三次请求")],
            "unmeasurable": [("M7", "ssl 不暴露 CertificateRequest")],
            "unreachable": [("csp_absent", "选择器 `html_document` 不在契约字表")],
        },
    )
    md = render_markdown(report)
    assert "### 能力边界" in md
    assert "`S3`" in md and "三次请求" in md
    assert "`M7`" in md and "CertificateRequest" in md
    assert "不在契约字表" in md
    # 必须说清"不算未覆盖，也不算已覆盖"——否则读者会把它读成其中一种
    assert "不算未覆盖" in md


# ── B397：三条路径的报告必须说同一件事 ─────────────────────────
def test_limits_block_is_shared_by_run_hunt_and_platform() -> None:
    """`run` / `hunt` / `platform` 的报告都要有「能力边界」，且文案同源。

    同一个产品在三条路径上对"能测什么"的表述不一致，是最伤交付信任的一类问题。
    """
    from hos_ares.report.hunt import render_hunt_markdown
    from hos_ares.report.limits import capability_limits, limits_block

    block = "\n".join(limits_block(capability_limits()))
    assert "### 能力边界" in block
    assert "不算未覆盖" in block

    hunt_md = render_hunt_markdown(
        {"target_host": "h", "faces_run": [], "coverage": {}, "findings": {}},
        capsules_dir="capsules", generated_at="2026-01-01T00:00:00Z",
    )
    assert "### 能力边界" in hunt_md
    # 同一段文案：hunt 里也能读到 run 报告那句话
    assert "不算未覆盖" in hunt_md
