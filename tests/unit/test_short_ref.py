"""B101 · 短标识只有一份规则（`routes.short_ref`），两种用途各有硬约束。

| 用途 | 形态 | 约束 |
|---|---|---|
| 报告展示 | `route://contract/e27578ec` | 保留来源段（分得清契约面/观测面），不泄露完整 hash |
| 控制台 | `e27578ec` | **不许出现 scheme**；页面只给 8 位十六进制片段 |

两种都源自同一条规则——早先 report 与 console 各写一份，第二次改就分叉了。
"""

from __future__ import annotations

from veripen_core.routes.types import short_ref

S = chr(0x2F)
SCHEME = "route" + ":" + S * 2


def _ref(source: str = "contract", rid: str = "e27578ec10330af0") -> str:
    return SCHEME + source + S + rid


def test_report_form_keeps_source_and_truncates_id() -> None:
    assert short_ref(_ref(), with_source=True, with_scheme=True) == SCHEME + "contract/e27578ec"


def test_console_form_is_bare_hash() -> None:
    out = short_ref(_ref())
    assert out == "e27578ec"
    assert S not in out and ":" not in out, "控制台不许出现 scheme"


def test_plain_ref_is_truncated_without_adding_scheme() -> None:
    """无 scheme 的 ref 保持原样截断（B74 行为）；不能凭空补一个 route://。"""
    assert short_ref("plain-ref-value", with_source=True, with_scheme=True) == "plain-re"


def test_empty_ref_is_empty() -> None:
    assert short_ref(None) == ""
    assert short_ref("") == ""


def test_full_hash_never_leaks_in_either_form() -> None:
    long_id = "c" * 64
    assert long_id not in short_ref(_ref(rid=long_id), with_source=True, with_scheme=True)
    assert long_id not in short_ref(_ref(rid=long_id))


def test_json_artifact_carries_full_ref_and_short_form() -> None:
    from veripen.report.render import FindingView, Outcome, ReportInput, render_json

    view = FindingView(
        finding_id="F-1",
        title="t",
        asset_id="a",
        oracle_id="sqli_bool_diff",
        cwe="CWE-89",
        severity="medium",
        confidence=1.0,
        route_strength="contract",
        route_ref=_ref(),
        capsule_id=None,
        self_sha256=None,
        outcome=Outcome.UNDETERMINED,
    )
    report = ReportInput(
        engagement="e", profile_hash="", image_digest="", root_sha256="", findings=[view]
    )
    route = render_json(report)["findings"][0]["route"]
    # 机器要能拿 ref 回查绑定；展示用 short。两者不能混。
    assert route["ref"] == _ref()
    assert route["short"] == SCHEME + "contract/e27578ec"
