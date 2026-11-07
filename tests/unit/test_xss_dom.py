"""B117 · `xss_dom` 判定：只看标记位，三条件缺一不判。

判定输入是渲染传输合成的 body（`{"marker": …}`），所以这一组全是纯函数测试——
不需要浏览器；浏览器那条路由 `test_render_transport.py` 覆盖。
"""

from __future__ import annotations

import json

import pytest

from veripen_core.models import Verdict
from veripen_oracles.common.harness import Pair, Trace, Variant
from veripen_oracles.tier_dom import MARKER, PAYLOAD, XssDom


def _trace(marker: str, *, status: int = 200, error: str | None = None) -> Trace:
    return Trace(
        route_ref="route" + ":" + chr(0x2F) * 2 + "crawl/deadbeef",
        status=status,
        body=json.dumps({"marker": marker}).encode(),
        error=error,
    )


def _pair(role: str, marker: str, *, error: str | None = None) -> Pair:
    variant = Variant(name=f"dom_{role}", role=role)
    return Pair(variant=variant, baseline=_trace(""), trace=_trace(marker, error=error))


def _judge(probe: str, baseline: str = "", control: str = "", *, error: str | None = None):  # noqa: ANN202
    return XssDom().judge([
        _pair("baseline", baseline),
        _pair("probe", probe, error=error),
        _pair("control", control),
    ])


def test_marker_fired_is_verified() -> None:
    verdict = _judge(probe="1")
    assert verdict.verdict is Verdict.VERIFIED
    assert verdict.confidence >= 0.8
    assert verdict.assertion["observed"]["probe_marker"] == "1"


def test_no_marker_is_refuted() -> None:
    assert _judge(probe="").verdict is Verdict.REFUTED


def test_baseline_firing_refutes() -> None:
    """基线本来就写标记位 → 说明不归因于载荷。"""
    assert _judge(probe="1", baseline="1").verdict is Verdict.REFUTED


def test_control_firing_refutes() -> None:
    """对照也写 → 标记位自己抖动，不是载荷被执行。"""
    assert _judge(probe="1", control="1").verdict is Verdict.REFUTED


def test_render_error_is_inconclusive_not_refuted() -> None:
    """渲染失败 ≠ 没有漏洞（D1）。"""
    assert _judge(probe="", error="render_timeout").verdict is Verdict.INCONCLUSIVE


def test_missing_variant_is_inconclusive() -> None:
    assert XssDom().judge([_pair("probe", "1")]).verdict is Verdict.INCONCLUSIVE


def test_body_that_is_not_json_counts_as_no_marker() -> None:
    bad = Trace(route_ref="r", status=200, body=b"<html>not json")
    pair = Pair(variant=Variant(name="dom_probe", role="probe"), baseline=bad, trace=bad)
    verdict = XssDom().judge([
        Pair(variant=Variant(name="dom_baseline", role="baseline"), baseline=bad, trace=bad),
        pair,
        Pair(variant=Variant(name="dom_control", role="control"), baseline=bad, trace=bad),
    ])
    assert verdict.verdict is Verdict.REFUTED


def test_payload_targets_the_marker() -> None:
    assert MARKER in PAYLOAD
    assert "onerror" in PAYLOAD


@pytest.mark.parametrize("role", ["baseline", "probe", "control"])
def test_plan_covers_three_roles(role: str) -> None:
    names = {v.role for v in XssDom().plan(object())}
    assert names == {"baseline", "probe", "control"}
