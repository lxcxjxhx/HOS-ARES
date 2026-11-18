"""B219：SARIF 是给 CI / 代码扫描看的，描述与消息不许是占位符。"""

from __future__ import annotations

import json

from veripen.report.render import (
    FindingView,
    Outcome,
    ReportInput,
    render_sarif,
)


def _report() -> ReportInput:
    finding = FindingView(
        finding_id="F-1", title="idor_cross_account 已验证", asset_id="web-01",
        oracle_id="idor_cross_account", cwe="CWE-639", severity="high", confidence=0.95,
        route_strength="contract", route_ref="route://contract/abc", capsule_id="c1",
        self_sha256="a" * 64, replay_status="OK",
        situation="secondary 读取 primary 的对象成功，且访问无权对象被拒",
        outcome=Outcome.REPORTED,
    )
    return ReportInput(
        engagement="ENG-1", profile_hash="h", image_digest="d", root_sha256="b" * 64,
        routeset_sha256=["c"], resolver_version="1", render_time="t", distribution="internal",
        findings=[finding],
    )


def test_rule_description_is_not_the_oracle_id() -> None:
    rule = render_sarif(_report())["runs"][0]["tool"]["driver"]["rules"][0]
    short = rule["shortDescription"]["text"]
    assert short != "idor_cross_account", "描述又回落到 oracle id 了"
    assert len(short) > 10, short


def test_no_placeholder_help_uri() -> None:
    """`about:blank` 是一条点开空白的帮助链接——没有真链接就不写。"""
    rule = render_sarif(_report())["runs"][0]["tool"]["driver"]["rules"][0]
    assert rule.get("helpUri") != "about:blank"
    assert "helpUri" not in rule or str(rule["helpUri"]).startswith(("http", "https"))


def test_result_message_is_what_we_observed() -> None:
    """扫描界面那一行消息 = 现象；不是标题「xxx 已验证」。"""
    result = render_sarif(_report())["runs"][0]["results"][0]
    assert "secondary 读取 primary" in result["message"]["text"]
    assert result["message"]["text"] != "idor_cross_account 已验证"


# ── B250：driver 的版本与占位链接 ────────────────────────────────
def test_driver_carries_a_version() -> None:
    """SARIF 消费者靠 driver.version 把结果对到某个构建上；空着就等于没有出处。"""
    driver = render_sarif(_report())["runs"][0]["tool"]["driver"]
    assert driver.get("version"), driver
    assert str(driver["version"]).strip() == driver["version"], "版本里不能带空白"


def test_driver_has_no_placeholder_information_uri() -> None:
    """规则层的 `helpUri` 早就不许写 `about:blank`（B219）；driver 同一原则。"""
    driver = render_sarif(_report())["runs"][0]["tool"]["driver"]
    uri = driver.get("informationUri")
    assert uri != "about:blank", driver
    assert uri is None or str(uri).startswith(("http", "https")), driver


# ── B373：没成结论的，不许当成 finding 报出去 ────────────────────
def _finding(fid: str, *, outcome: Outcome, refuted: bool = False, reason: str = "") -> FindingView:
    # REPORTED 必须带证据指针，否则 render 期的 09 §7 断言会（正确地）拒绝渲染
    reported = outcome is Outcome.REPORTED
    return FindingView(
        finding_id=fid, title="t", asset_id="web-01", oracle_id="sqli_bool_diff",
        cwe="CWE-89", severity="high", confidence=0.9 if reported else 0.0,
        route_strength="contract", route_ref="route://contract/abc",
        capsule_id="sha256:" + "d" * 64 if reported else None,
        self_sha256="d" * 64 if reported else None,
        replay_status="NEVER", situation="一句话", outcome=outcome, refuted=refuted,
        reason_code=reason,
    )


def _report_with(*findings: FindingView) -> ReportInput:
    return ReportInput(
        engagement="ENG-1", profile_hash="h", image_digest="d", root_sha256="b" * 64,
        routeset_sha256=["c"], resolver_version="1", render_time="t", distribution="internal",
        findings=list(findings),
    )


def test_only_reported_findings_become_results() -> None:
    """实测：安全靶与"连不上"的靶，SARIF 里都各有 3 条 `level=error` 的 result。

    CI 消费者（GitHub code scanning、DefectDojo、SIEM）按 result 计数——那等于
    把"已判否"和"目标没连上"当成三个漏洞报出去。
    """
    sarif = render_sarif(_report_with(
        _finding("F-1", outcome=Outcome.REPORTED),
        _finding("F-2", outcome=Outcome.NOT_RUN, refuted=True, reason="control_also_differs"),
        _finding("F-3", outcome=Outcome.ERROR, reason="transport_error:connection_refused"),
    ))
    results = sarif["runs"][0]["results"]
    assert len(results) == 1, [r["ruleId"] for r in results]
    assert results[0]["properties"]["outcome"] == "reported"


def test_non_findings_are_still_listed_so_nothing_is_silently_dropped() -> None:
    sarif = render_sarif(_report_with(
        _finding("F-1", outcome=Outcome.REPORTED),
        _finding("F-2", outcome=Outcome.NOT_RUN, refuted=True, reason="control_also_differs"),
        _finding("F-3", outcome=Outcome.ERROR, reason="transport_error:connection_refused"),
    ))
    not_reported = sarif["runs"][0]["properties"]["notReported"]
    assert [n["outcome"] for n in not_reported] == ["not_run", "error"]
    assert not_reported[0]["refuted"] is True
    assert not_reported[1]["reason"].startswith("transport_error:")


def test_broken_run_is_not_reported_as_a_clean_scan() -> None:
    """`results: []` 在扫描界面里读作"没有发现"。

    目标连不上时结果也是空的——所以必须让消费者能区分：`invocations.executionSuccessful`
    为 False，并附一条 error 级通知（B373）。
    """
    broken = render_sarif(_report_with(
        _finding("F-9", outcome=Outcome.ERROR, reason="transport_error:connection_refused")))
    inv = broken["runs"][0]["invocations"][0]
    assert inv["executionSuccessful"] is False
    assert inv["toolExecutionNotifications"][0]["level"] == "error"
    assert "不等于没有漏洞" in inv["toolExecutionNotifications"][0]["message"]["text"]

    clean = render_sarif(_report_with(_finding("F-1", outcome=Outcome.REPORTED)))
    inv2 = clean["runs"][0]["invocations"][0]
    assert inv2["executionSuccessful"] is True
    assert inv2["toolExecutionNotifications"] == []


# ── B374：codeFlows 承载三条件证据链 ────────────────────────────
def _report_with_traces() -> ReportInput:
    f = FindingView(
        finding_id="F-1", title="t", asset_id="web-01", oracle_id="sqli_bool_diff",
        cwe="CWE-89", severity="high", confidence=0.9, route_strength="contract",
        route_ref="route://contract/abc", capsule_id="sha256:" + "d" * 64,
        self_sha256="d" * 64, replay_status="NEVER", situation="一句话",
        outcome=Outcome.REPORTED,
        traces=(
            {"role": "baseline", "status": 200, "bytes": 99},
            {"role": "control", "status": 200, "bytes": 99},
            {"role": "probe", "status": 200, "bytes": 12},
        ),
    )
    return ReportInput(
        engagement="ENG-1", profile_hash="h", image_digest="d", root_sha256="b" * 64,
        routeset_sha256=["c"], resolver_version="1", render_time="t",
        distribution="internal", findings=[f],
    )


def test_code_flows_carry_the_three_condition_chain() -> None:
    """CI 侧要能读"凭什么判的"：baseline → control → probe，各一行。"""
    result = render_sarif(_report_with_traces())["runs"][0]["results"][0]
    locs = result["codeFlows"][0]["threadFlows"][0]["locations"]
    assert [l["location"]["properties"]["role"] for l in locs] == [
        "baseline", "control", "probe"]
    assert locs[0]["location"]["physicalLocation"]["region"]["startLine"] == 1
    assert locs[-1]["location"]["physicalLocation"]["region"]["startLine"] == 3


def test_code_flows_do_not_leak_the_target_address() -> None:
    result = render_sarif(_report_with_traces())["runs"][0]["results"][0]
    text = json.dumps(result["codeFlows"], ensure_ascii=False)
    assert "http" not in text, "证据链里混进了地址"
    assert "asset:web-01" in text, "面要用资产标识，不给地址"


def test_no_traces_means_no_empty_code_flow() -> None:
    """没有观测链就不写这一段：空 codeFlow 会让扫描界面显示"证据链 · 0 步"。"""
    result = render_sarif(_report_with(_finding("F-1", outcome=Outcome.REPORTED)))[
        "runs"][0]["results"][0]
    assert "codeFlows" not in result


# ── B398：能力边界也要进 SARIF ─────────────────────────────────
def test_sarif_carries_capability_limits() -> None:
    """SARIF 消费者只读 SARIF：报告里那节它们看不到。

    缺了这段，「results 为空」会被 code scanning 读成"很干净"，
    而真相可能是"这一轮根本测不了"。
    """
    from veripen.report.render import ReportInput, render_sarif

    report = ReportInput(
        engagement="demo", profile_hash="", image_digest="", root_sha256="",
        capability_limits={"needs_sequence": [("S3", "登出后再用旧会话")],
                           "unmeasurable": [("M7", "ssl 不暴露 CertificateRequest")]},
    )
    sarif = render_sarif(report)
    limits = sarif["runs"][0]["properties"]["capabilityLimits"]
    assert {"kind": "needs_sequence", "check": "S3", "why": "登出后再用旧会话"} in limits
    assert any(item["check"] == "M7" for item in limits), limits
