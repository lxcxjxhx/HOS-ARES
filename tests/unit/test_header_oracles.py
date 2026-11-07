"""B124 · 响应头三件套：CSP / 点击劫持防护 / nosniff。

三条共用同一骨架（`_HeaderAbsent`），但**各自的"有保护"判据不同**：
- CSP：有头且非空；
- 点击劫持：有 `X-Frame-Options` **或** CSP 里有 `frame-ancestors`（现代写法）；
- nosniff：值必须是 `nosniff`（写成 `none` 等于没配）。
"""

from __future__ import annotations

import pytest

from veripen_core.models import Verdict
from veripen_oracles.common.harness import Pair, Trace, Variant
from veripen_oracles.tier_headers import CspAbsent, FrameOptAbsent, NosniffAbsent

S = chr(0x2F)


def _trace(headers: dict[str, str], *, status: int = 200, error: str | None = None) -> Trace:
    return Trace(
        route_ref="route" + ":" + S * 2 + "crawl/deadbeef",
        status=status,
        body=b"<html></html>",
        headers=headers,
        error=error,
    )


def _judge(oracle, probe: dict[str, str], control: dict[str, str], *,  # noqa: ANN001, ANN202
           error: str | None = None):
    def pair(role: str, headers: dict[str, str]) -> Pair:
        return Pair(
            variant=Variant(name=f"hdr_{role}", role=role),
            baseline=_trace({}),
            trace=_trace(headers, error=error if role == "probe" else None),
        )

    return oracle.judge([pair("probe", probe), pair("control", control)])


# ── CSP ────────────────────────────────────────────────────────
def test_csp_absent_both_requests_is_verified() -> None:
    verdict = _judge(CspAbsent(), {}, {})
    assert verdict.verdict is Verdict.VERIFIED
    assert verdict.reason == "csp_header_absent"


def test_csp_present_refutes() -> None:
    assert _judge(CspAbsent(), {"Content-Security-Policy": "default-src 'self'"},
                  {}).verdict is Verdict.REFUTED


def test_csp_header_name_case_insensitive() -> None:
    assert _judge(CspAbsent(), {"CONTENT-SECURITY-POLICY": "x"}, {}).verdict is Verdict.REFUTED


def test_csp_empty_value_counts_as_absent() -> None:
    assert _judge(CspAbsent(), {"Content-Security-Policy": "  "}, {}).verdict is Verdict.VERIFIED


# ── 点击劫持防护 ────────────────────────────────────────────────
def test_frame_absent_when_neither_header_nor_csp() -> None:
    verdict = _judge(FrameOptAbsent(), {}, {})
    assert verdict.verdict is Verdict.VERIFIED
    assert verdict.reason == "frame_protection_absent"


def test_frame_xfo_counts() -> None:
    assert _judge(FrameOptAbsent(), {"X-Frame-Options": "DENY"}, {}).verdict is Verdict.REFUTED


def test_frame_csp_frame_ancestors_counts() -> None:
    """现代写法：CSP 的 frame-ancestors 同样拦住点击劫持。"""
    headers = {"Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'"}
    assert _judge(FrameOptAbsent(), headers, {}).verdict is Verdict.REFUTED


def test_frame_csp_without_frame_ancestors_still_absent() -> None:
    headers = {"Content-Security-Policy": "default-src 'self'"}
    assert _judge(FrameOptAbsent(), headers, {}).verdict is Verdict.VERIFIED


# ── nosniff ────────────────────────────────────────────────────
def test_nosniff_absent_is_verified() -> None:
    verdict = _judge(NosniffAbsent(), {}, {})
    assert verdict.verdict is Verdict.VERIFIED
    assert verdict.reason == "nosniff_absent"


def test_nosniff_value_must_be_nosniff() -> None:
    assert _judge(NosniffAbsent(), {"X-Content-Type-Options": "nosniff"},
                  {}).verdict is Verdict.REFUTED
    assert _judge(NosniffAbsent(), {"X-Content-Type-Options": "none"},
                  {}).verdict is Verdict.VERIFIED


# ── 共同行为 ────────────────────────────────────────────────────
@pytest.mark.parametrize("oracle", [CspAbsent(), FrameOptAbsent(), NosniffAbsent()])
def test_error_is_inconclusive(oracle) -> None:  # noqa: ANN001
    assert _judge(oracle, {}, {}, error="timeout").verdict is Verdict.INCONCLUSIVE


_PROTECTED = {
    "csp_absent": {"Content-Security-Policy": "default-src 'self'"},
    "frame_opt_absent": {"X-Frame-Options": "DENY"},
    "nosniff_absent": {"X-Content-Type-Options": "nosniff"},
}


@pytest.mark.parametrize("oracle", [CspAbsent(), FrameOptAbsent(), NosniffAbsent()],
                         ids=lambda o: o.meta.id)
def test_protection_on_control_only_also_refutes(oracle) -> None:  # noqa: ANN001
    """只在一半响应上有保护 = 不稳定，不判"缺失"。"""
    assert _judge(oracle, {}, _PROTECTED[oracle.meta.id]).verdict is Verdict.REFUTED


@pytest.mark.parametrize("oracle", [CspAbsent(), FrameOptAbsent(), NosniffAbsent()])
def test_missing_probe_is_inconclusive(oracle) -> None:  # noqa: ANN001
    pair = Pair(variant=Variant(name="hdr_control", role="control"),
                baseline=_trace({}), trace=_trace({}))
    assert oracle.judge([pair]).verdict is Verdict.INCONCLUSIVE
