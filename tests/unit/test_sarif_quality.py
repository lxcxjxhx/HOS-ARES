"""B219：SARIF 是给 CI / 代码扫描看的，描述与消息不许是占位符。"""

from __future__ import annotations

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
