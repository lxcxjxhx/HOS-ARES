"""B123 · `csp_absent`：只看响应头，probe 与 control 两次都没有才算稳定缺失。"""

from __future__ import annotations

from veripen_core.models import Verdict
from veripen_oracles.common.harness import Pair, Trace, Variant
from veripen_oracles.tier_headers import CspAbsent

S = chr(0x2F)


def _trace(headers: dict[str, str], *, status: int = 200, error: str | None = None) -> Trace:
    return Trace(
        route_ref="route" + ":" + S * 2 + "crawl/deadbeef",
        status=status,
        body=b"<html></html>",
        headers=headers,
        error=error,
    )


def _pair(role: str, headers: dict[str, str], *, error: str | None = None) -> Pair:
    return Pair(
        variant=Variant(name=f"csp_{role}", role=role),
        baseline=_trace({}),
        trace=_trace(headers, error=error),
    )


def _judge(probe: dict[str, str], control: dict[str, str], *,
           error: str | None = None):  # noqa: ANN202
    return CspAbsent().judge([
        _pair("probe", probe, error=error),
        _pair("control", control),
    ])


def test_absent_on_both_requests_is_verified() -> None:
    verdict = _judge({}, {})
    assert verdict.verdict is Verdict.VERIFIED
    assert verdict.reason == "csp_header_absent"
    assert verdict.assertion["fingerprint"]["csp_absent"] is True


def test_header_present_refutes() -> None:
    verdict = _judge({"Content-Security-Policy": "default-src 'self'"}, {})
    assert verdict.verdict is Verdict.REFUTED
    assert verdict.reason == "csp_header_present"


def test_header_on_control_also_refutes() -> None:
    """只在一半响应上出现，说明不稳定——不判"缺失"。"""
    assert _judge({}, {"content-security-policy": "default-src 'none'"}).verdict is Verdict.REFUTED


def test_header_name_is_case_insensitive() -> None:
    assert _judge({"CONTENT-SECURITY-POLICY": "x"}, {}).verdict is Verdict.REFUTED


def test_empty_header_value_counts_as_absent() -> None:
    """头在但值为空，等于没配。"""
    assert _judge({"Content-Security-Policy": "   "}, {}).verdict is Verdict.VERIFIED


def test_fetch_error_is_inconclusive() -> None:
    assert _judge({}, {}, error="timeout").verdict is Verdict.INCONCLUSIVE


def test_missing_probe_is_inconclusive() -> None:
    """框架要求 probe 存在；只给 control 会落到 three_way_failed（第一版踩过）。"""
    assert CspAbsent().judge([_pair("control", {})]).verdict is Verdict.INCONCLUSIVE


# ── 标签：HTML 页面现在有结构化标签 ──────────────────────────────
def test_html_pages_get_the_document_tag() -> None:
    from veripen_core.routes.tagging import infer_tags

    tags = infer_tags(method="GET", template=S + "about", param_names=(),
                      content_type="text/html; charset=utf-8", fields={})
    assert "html_document" in tags


def test_json_pages_do_not_get_it() -> None:
    from veripen_core.routes.tagging import infer_tags

    tags = infer_tags(method="GET", template=S + "api", param_names=(),
                      content_type="application/json", fields={})
    assert "html_document" not in tags
