"""B125 · HSTS：只在 https 上判，http 上说"不适用"。

这条的价值一半在判据，一半在**敢说"不适用"**：
http 目标上问"缺不缺 HSTS"没有意义（浏览器不认），judge 必须返回 INCONCLUSIVE
并写清原因，而不是顺手判否——那会把每个 http 站点都标成"缺 HSTS"的假阳性。
"""

from __future__ import annotations

from veripen_core.models import Verdict
from veripen_oracles.common.harness import Pair, Trace, Variant
from veripen_oracles.tier_headers import HstsAbsent

S = chr(0x2F)


def _trace(headers: dict[str, str], *, scheme: str = "https",
           status: int = 200, error: str | None = None) -> Trace:
    return Trace(
        route_ref="route" + ":" + S * 2 + "crawl/deadbeef",
        status=status,
        body=b"<html></html>",
        headers=headers,
        scheme=scheme,
        error=error,
    )


def _judge(probe: dict[str, str], control: dict[str, str] | None = None, *,
           scheme: str = "https", error: str | None = None):  # noqa: ANN202
    def pair(role: str, headers: dict[str, str]) -> Pair:
        return Pair(
            variant=Variant(name=f"hdr_{role}", role=role),
            baseline=_trace({}, scheme=scheme),
            trace=_trace(headers, scheme=scheme, error=error if role == "probe" else None),
        )

    return HstsAbsent().judge([pair("probe", probe), pair("control", control or {})])


def test_https_without_sts_is_verified() -> None:
    verdict = _judge({})
    assert verdict.verdict is Verdict.VERIFIED
    assert verdict.reason == "hsts_absent"


def test_https_with_sts_refutes() -> None:
    verdict = _judge({"Strict-Transport-Security": "max-age=31536000"})
    assert verdict.verdict is Verdict.REFUTED
    assert verdict.reason == "hsts_present"


def test_max_age_zero_counts_as_disabled() -> None:
    """`max-age=0` 是关掉 HSTS 的正式写法：头在，等于没有。"""
    assert _judge({"Strict-Transport-Security": "max-age=0"}).verdict is Verdict.VERIFIED


def test_non_https_is_inconclusive_not_refuted() -> None:
    """http 上问"缺不缺 HSTS"没有意义——判否会变成假阳性。"""
    verdict = _judge({}, scheme="http")
    assert verdict.verdict is Verdict.INCONCLUSIVE
    assert verdict.reason == "not_https"
    assert verdict.assertion["fingerprint"]["applicable"] is False


def test_header_on_control_only_also_refutes() -> None:
    assert _judge({}, {"Strict-Transport-Security": "max-age=600"}).verdict is Verdict.REFUTED


def test_fetch_error_is_inconclusive() -> None:
    assert _judge({}, error="timeout").verdict is Verdict.INCONCLUSIVE


# ── scheme 由传输层如实上报 ─────────────────────────────────────
def test_trace_defaults_scheme_to_empty() -> None:
    """老调用方不传 scheme 时是空串——判据必须能处理"未知"。"""
    assert Trace(route_ref="r", status=200).scheme == ""
