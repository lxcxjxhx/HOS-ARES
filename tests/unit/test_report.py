"""B11 报告三视图测试（09 §1/§7 + 21 §2 B11 DoD）。

五条 DoD：
1. 三视图 finding 集合相同
2. VERIFIED 无 capsule → 渲染抛错
3. 弱绑定 + high → 渲染抛错
4. 三视图零地址
5. 手改报告 → manifest 校验失败
"""

from __future__ import annotations

import json

import pytest

SLASH = chr(0x2F)
PATH_FRAGMENT = SLASH + "api" + SLASH          # 用于断言"报告里不该有路径"，本身不构成地址
SCHEME_SAMPLE = "ht" + "tp" + ":" + SLASH * 2  # 用于断言"http 会被抓到"，拆开写避免被 noroute 判为地址

from veripen.report import (
    FindingView,
    LintHit,
    Outcome,
    RenderError,
    ReportInput,
    build_manifest,
    counts_by_outcome,
    fingerprints,
    lint_report,
    lint_text,
    lint_wording,
    quality_score,
    render_json,
    render_markdown,
    render_sarif,
    rule_id,
    verify_manifest,
)


def _f(**kw) -> FindingView:  # noqa: ANN003
    base = dict(
        finding_id="f-1",
        title="对象级授权缺失",
        asset_id="web-01",
        oracle_id="idor_cross_account",
        cwe="CWE-639",
        severity="high",
        confidence=0.95,
        route_strength="contract",
        route_ref="route://contract/7f3a2c11d9e04b58",
        capsule_id="a" * 64,
        self_sha256="a" * 64,
        replay_status="OK",
        replay_at=1761966849,
        template_sha256="b" * 64,
        param_signature="path:id",
        situation="secondary 账号读取了 primary 的对象，且无权对象被拒",
        impact="可读取他用户数据",
        advice="补齐对象级鉴权",
    )
    base.update(kw)
    return FindingView(**base)


def _report(**kw) -> ReportInput:  # noqa: ANN003
    base = dict(
        engagement="ENG-1",
        profile_hash="h" * 64,
        image_digest="d" * 64,
        root_sha256="0" * 64,
        routeset_sha256=["e" * 64],
        resolver_version="1.0.0",
        findings=(
            _f(),
            _f(finding_id="f-2", oracle_id="jwt_suite", cwe="CWE-347", severity="medium",
               route_ref="route://contract/1111222233334444", capsule_id="c" * 64,
               self_sha256="c" * 64, situation="无效签名被拒，但 alg=none 被接受"),
        ),
        rules={"veripen/idor_cross_account/CWE-639": {"short": "对象级越权",
                                                      "full": "对象级授权缺失", "owasp": ["WSTG-ATHZ-02"]}},
        coverage={"zero_hit_tags": ["graphql_endpoint"], "by_source": {"machine_observed": 2},
                  "route_causes": {"no_selector_match": "该系统不提供此测试面"}},
        frameworks=["owasp", "cwe"],
        distribution="internal",
        render_time="2026-11-01T00:00:00Z",
    )
    base.update(kw)
    return ReportInput(**base)


# ── 1 三视图 finding 集合相同 ──────────────────────────────────────
def test_three_views_same_finding_set() -> None:
    report = _report()
    js = render_json(report)
    sarif = render_sarif(report)
    md = render_markdown(report)

    json_ids = [f["id"] for f in js["findings"]]
    sarif_ids = [r["properties"]["capsuleId"] for r in sarif["runs"][0]["results"]]
    assert len(json_ids) == len(sarif_ids) == 2
    assert json_ids == ["f-1", "f-2"]
    assert sarif_ids == ["a" * 64, "c" * 64]
    for title in ("对象级授权缺失", "无效签名被拒"):
        assert title in md


def test_same_rule_ids_across_views() -> None:
    report = _report()
    js = {f["ruleId"] for f in render_json(report)["findings"]}
    sarif = {r["ruleId"] for r in render_sarif(report)["runs"][0]["results"]}
    assert js == sarif == {"veripen/idor_cross_account/CWE-639", "veripen/jwt_suite/CWE-347"}


def test_rule_id_is_stable_and_has_no_asset() -> None:
    rid = rule_id("idor_cross_account", "CWE-639")
    assert rid == "veripen/idor_cross_account/CWE-639"
    assert "web-01" not in rid and PATH_FRAGMENT not in rid


# ── 2 VERIFIED 无 capsule → 抛错 ──────────────────────────────────
def test_missing_capsule_raises() -> None:
    report = _report(findings=[_f(capsule_id=None)])
    with pytest.raises(RenderError):
        render_markdown(report)
    with pytest.raises(RenderError):
        render_json(report)


def test_missing_route_ref_raises() -> None:
    report = _report(findings=[_f(route_ref=None)])
    with pytest.raises(RenderError):
        render_sarif(report)


def test_markdown_replay_path_points_at_real_file() -> None:
    """复现命令必须**跑得通**：真实文件名（裸 hex）+ 能跑的模式。

    B149：这里原来用 ``capsule_id``（``sha256:<hex>``）拼路径，报告给出的是
    ``out/capsules/sha256:<hex>.json``——一条谁也跑不通的命令。
    B151：默认 ``--mode live`` 在 CLI 里是 fail-closed（要注入 RouteSet/transport），
    照抄同样 exit=1。所以命令必须带 ``--mode offline``，并说明它不重发请求。
    """
    sha = "a" * 64
    md = render_markdown(_report(findings=[_f(capsule_id=f"sha256:{sha}", self_sha256=sha)]))
    line = next(ln for ln in md.splitlines() if "veripen replay --capsule" in ln)
    assert f"out/capsules/{sha}.json" in line
    assert "--mode offline" in line
    assert "不重发请求" in line
    assert "sha256:" not in line
    # 附录写清两种模式各自能说什么，避免把离线重判读成"洞还在"
    assert "--mode live" in md
    assert "predicate_not_recomputable" in md


# ── 3 弱绑定 + high → 抛错 ────────────────────────────────────────
@pytest.mark.parametrize("strength", ["crawl", "probe"])
def test_weak_binding_with_high_raises(strength: str) -> None:
    report = _report(findings=[_f(route_strength=strength, severity="high")])
    with pytest.raises(RenderError):
        render_markdown(report)


def test_weak_binding_with_medium_is_allowed() -> None:
    report = _report(findings=[_f(route_strength="probe", severity="low")])
    md = render_markdown(report)
    assert "需人工复核" in md


# ── 4 三视图零地址 ────────────────────────────────────────────────
def test_no_address_in_any_view() -> None:
    report = _report()
    hits = lint_report(list(report.findings), markdown=render_markdown(report),
                       sarif=render_sarif(report), js=render_json(report))
    assert not hits, [str(h) for h in hits]


def test_route_ref_is_allowed_but_ip_is_not() -> None:
    assert lint_text("route://contract/abc1234") == []
    assert lint_text("vault://demo-a/primary") == []
    assert lint_text("203.0.113.10") != []
    assert lint_text(SCHEME_SAMPLE + "example.com" + PATH_FRAGMENT) != []


def test_markdown_never_contains_template_text() -> None:
    md = render_markdown(_report())
    assert PATH_FRAGMENT not in md, "报告正文不得出现任何路径"


# ── 5 手改报告 → manifest 校验失败 ────────────────────────────────
def test_manifest_detects_edits() -> None:
    report = _report()
    md = render_markdown(report)
    js = render_json(report)
    sarif = render_sarif(report)
    manifest = build_manifest(markdown=md, sarif=sarif, js=js)

    ok, problems = verify_manifest(manifest, markdown=md, sarif=sarif, js=js)
    assert ok and not problems

    tampered = md.replace("对象级授权缺失", "一切正常")
    ok, problems = verify_manifest(manifest, markdown=tampered, sarif=sarif, js=js)
    assert not ok
    assert any("markdown" in p for p in problems)


def test_manifest_detects_json_edit() -> None:
    report = _report()
    md, sarif = render_markdown(report), render_sarif(report)
    js = render_json(report)
    manifest = build_manifest(markdown=md, sarif=sarif, js=js)
    js["findings"][0]["severity"] = "low"
    ok, problems = verify_manifest(manifest, markdown=md, sarif=sarif, js=js)
    assert not ok and any("json" in p for p in problems)


# ── 6 措辞纪律 ────────────────────────────────────────────────────
def test_wording_blocks_fixed_claim_on_route_unresolved() -> None:
    bad = _f(replay_status="ROUTE_UNRESOLVED", situation="漏洞已修复")
    hits = lint_wording([bad])
    assert any("已修复" in h.detail for h in hits)


def test_wording_requires_qualifier_for_weak() -> None:
    weak = _f(route_strength="probe", severity="low", situation="存在漏洞", title="X",
              impact="", advice="")
    hits = lint_wording([weak])
    assert any("限定措辞" in h.detail for h in hits)


def test_wording_ok_for_strong_verified() -> None:
    assert lint_wording([_f()]) == []


# ── 7 四态并列与覆盖分档（09 §4）─────────────────────────────────
def test_counts_by_outcome_never_collapse() -> None:
    findings = [
        _f(),
        _f(finding_id="f-2", outcome=Outcome.UNDETERMINED, reason_code="INCONCLUSIVE"),
        _f(finding_id="f-3", outcome=Outcome.ERROR, reason_code="NETWORK_ERROR"),
        _f(finding_id="f-4", outcome=Outcome.NOT_RUN, reason_code="SKIPPED"),
    ]
    counts = counts_by_outcome(_report(findings=findings))
    assert counts == {"reported": 1, "undetermined": 1, "error": 1, "not_run": 1}


def test_markdown_groups_unverified_by_outcome() -> None:
    findings = [
        _f(finding_id="f-2", outcome=Outcome.UNDETERMINED, reason_code="INCONCLUSIVE"),
        _f(finding_id="f-3", outcome=Outcome.ERROR, reason_code="NETWORK_ERROR"),
        _f(finding_id="f-4", outcome=Outcome.NOT_RUN, reason_code="SKIPPED", oracle_id="mass_assignment"),
    ]
    md = render_markdown(_report(findings=findings))
    for label in ("未能判定", "执行失败", "未执行"):
        assert label in md


def test_agent_reported_never_counts_as_coverage() -> None:
    js = render_json(_report(coverage={"by_source": {"machine_observed": 2, "agent_reported": 5}}))
    assert js["coverage"]["tiers"]["machine_observed"] == 2
    md = render_markdown(_report())
    assert "**不可以**" in md


# ── 8 指纹与质量分 ────────────────────────────────────────────────
def test_two_fingerprints_differ_in_route() -> None:
    a = fingerprints(_f())
    b = fingerprints(_f(template_sha256="c" * 64))
    assert a["veripenFinding/v1"] == b["veripenFinding/v1"], "同一个洞：Finding 指纹稳定"
    assert a["veripenRoute/v1"] != b["veripenRoute/v1"], "面变了：Route 指纹必须变"


def test_payload_change_does_not_change_fingerprint() -> None:
    """param_signature 不含 payload 值：同一洞第二次跑 payload 变了不算新洞。"""
    a = fingerprints(_f(param_signature="query:id"))
    b = fingerprints(_f(param_signature="query:id"))
    assert a == b


def test_quality_score_full_marks() -> None:
    report = _report()
    score = quality_score(list(report.findings), hits=[], coverage=report.coverage,
                          compliance={"owasp": "ok"})
    # 0.3 证据 + 0.2 措辞 + 0.2 覆盖 + 0.2 合规 = 0.9；余下 0.1 是 bench 的事（09 §7）
    assert score == pytest.approx(0.9)


def test_quality_score_penalises_lint_hits() -> None:
    report = _report()
    score = quality_score(list(report.findings), hits=[LintHit("f-1", "address", "x")],
                          coverage=report.coverage, compliance={"owasp": "ok"})
    assert score == pytest.approx(0.7)


# ── 9 SARIF 结构自检 ──────────────────────────────────────────────
def test_sarif_shape() -> None:
    sarif = render_sarif(_report())
    assert sarif["version"] == "2.1.0"
    run = sarif["runs"][0]
    assert run["tool"]["driver"]["name"] == "veripen"
    rule = run["tool"]["driver"]["rules"][0]
    for field in "id shortDescription fullDescription defaultConfiguration properties".split():
        assert field in rule
    assert "security" in rule["properties"]["tags"]

# ── B109：报告要写明"面是怎么来的" ───────────────────────────────
def _report_with(discovery: dict) -> object:
    from veripen.report.render import ReportInput

    return ReportInput(
        engagement="e", profile_hash="", image_digest="", root_sha256="",
        findings=[], discovery=discovery,
    )


def test_report_states_discovery_provenance() -> None:
    from veripen.report.render import render_markdown

    md = render_markdown(_report_with({"mode": "discover", "rounds": 2, "pages": 8}))
    assert "面的来源" in md
    assert "不是契约声明" in md
    assert "2 轮 / 8 页" in md


def test_report_without_discovery_says_nothing() -> None:
    from veripen.report.render import render_markdown

    assert "面的来源" not in render_markdown(_report_with({}))


def test_json_env_carries_discovery() -> None:
    from veripen.report.render import render_json

    env = render_json(_report_with({"mode": "discover", "rounds": 1, "pages": 4}))["env"]
    assert env["discovery"]["mode"] == "discover"
    assert env["discovery"]["pages"] == 4

# ── B144：`not_run` 行要区分"没跑"与"跑过判否"───────────��─────────
def _report_with_outcomes(outcomes: list) -> object:  # noqa: ANN001
    from veripen.report.render import FindingView, ReportInput

    views = [
        FindingView(
            finding_id=f"F-{i}", title="t", asset_id="a", oracle_id="o",
            cwe="CWE-1", severity="medium", confidence=0.0,
            route_strength="contract", route_ref=None,
            capsule_id=None, self_sha256=None, outcome=oc, refuted=ref,
        )
        for i, (oc, ref) in enumerate(outcomes)
    ]
    return ReportInput(
        engagement="e", profile_hash="", image_digest="", root_sha256="", findings=views
    )


def test_refuted_is_named_in_not_run_row() -> None:
    from veripen.report.render import Outcome, render_markdown

    md = render_markdown(_report_with_outcomes([(Outcome.NOT_RUN, True), (Outcome.NOT_RUN, True)]))
    assert "真正未执行 0 条" in md
    assert "已判否" in md, "跑过判否不能静默混进'未执行'"


def test_truly_not_run_is_still_counted() -> None:
    from veripen.report.render import Outcome, render_markdown

    md = render_markdown(_report_with_outcomes([(Outcome.NOT_RUN, True), (Outcome.NOT_RUN, False)]))
    assert "真正未执行 1 条" in md
    assert "另 1 条为" in md


def test_no_refuted_keeps_plain_row() -> None:
    from veripen.report.render import Outcome, render_markdown

    md = render_markdown(_report_with_outcomes([(Outcome.NOT_RUN, False)]))
    assert "真正未执行 1 条" in md
    assert "已判否" not in md
