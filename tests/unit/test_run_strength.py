"""B98 · 面强度必须由置信档推出来，不能一律写 contract。

`RouteBinding` 没有 ``strength`` 字段，早期 `getattr(binding, "strength", "contract")`
于是把 **discovery 抓来的面（crawl 0.70）** 在报告里写成 **contract（0.90）**。
那是把"猜的面"和"契约声明的面"混成同一档，正是 09 §3.1 要防的事。

这条同时兜住一个潜在崩溃：渲染器规定弱面不得标 high/critical，
若强度写错，一条 crawl 面上的高危结论会让整份报告抛 RenderError。
"""

from __future__ import annotations

from typing import Any

import pytest

from veripen_core.routes.resolver import Resolver
from veripen_core.routes.sources import CrawlSource, StaticSource
from veripen_core.routes.sources.base import ObservedRecord
from veripen_core.routes.types import (
    AssetRef,
    RouteCandidate,
    RouteProvenance,
    RouteQuery,
)
from veripen_cli.cmd_run import _severity_of, _strength_of

S = chr(0x2F)


def p(*segments: str) -> str:
    return S + S.join(segments)


def _contract_binding() -> Any:
    cand = RouteCandidate(method="GET", template=p("api", "users", "{p1}"),
                          param_names=("p1",), source=RouteProvenance.CONTRACT)
    return Resolver(sources=[StaticSource("contract", RouteProvenance.CONTRACT, [cand])]).resolve(
        RouteQuery(asset=AssetRef(id="a"), min_confidence=0.0), now=1
    ).bindings[0]


def _crawl_binding() -> Any:
    rec = ObservedRecord(method="GET", template=p("api", "users", "{p1}"),
                         param_names=("p1",), content_type="application/json",
                         fields=("id",))
    return Resolver(sources=[CrawlSource([rec])]).resolve(
        RouteQuery(asset=AssetRef(id="a"), min_confidence=0.0), now=1
    ).bindings[0]


def test_contract_binding_is_contract_strength() -> None:
    assert _strength_of(_contract_binding()) == "contract"


def test_crawl_binding_is_not_reported_as_contract() -> None:
    """回归：discovery 面写成 contract 会把弱证据洗成强证据。"""
    assert _strength_of(_crawl_binding()) == "crawl"


class _Meta:
    def __init__(self, severity_rule: str) -> None:
        self.severity_rule = severity_rule


@pytest.mark.parametrize("rule", ["server_side_fetch", "cross_account_read"])
def test_weak_face_cannot_carry_high_severity(rule: str) -> None:
    severity, basis = _severity_of(_Meta(rule), "ssrf_oob", "crawl")
    assert severity == "medium" and basis == "weak_face_cap"


def test_contract_face_keeps_rule_severity() -> None:
    assert _severity_of(_Meta("server_side_fetch"), "ssrf_oob", "contract") == ("high", "rule")


def test_low_rule_is_untouched_on_weak_faces() -> None:
    assert _severity_of(_Meta("config_missing_rate_limit"), "rate_limit_absent", "crawl") == (
        "low",
        "rule",
    )


def test_unknown_rule_falls_back_with_visible_basis() -> None:
    assert _severity_of(_Meta("something_new"), "x", "contract") == ("medium", "default")


# ── B98：`_meta` 必须同时认 oracle 与 oracle.meta ───────────────────
def test_meta_reads_oracle_and_meta_shapes() -> None:
    from veripen_cli.cmd_run import _meta

    class _M:
        cwe = "CWE-639"
        severity_rule = "cross_account_read"

    class _O:
        meta = _M()

    assert _meta(_O(), "cwe") == "CWE-639"
    assert _meta(_M(), "cwe") == "CWE-639"


def test_meta_does_not_silently_return_default() -> None:
    """回归：再取一次 .meta 会得到 None，于是**每条** finding 的 cwe 都是空串。"""
    from veripen_cli.cmd_run import _meta

    class _M:
        cwe = "CWE-79"

    assert _meta(_M(), "cwe") != ""


# ── B100：finding id 里必须是面的可辨识尾巴，不能是 "route://" ──────
def test_ref_tail_is_not_the_scheme() -> None:
    from veripen_cli.cmd_run import _ref_tail

    class _B:
        ref = "route://contract/abcd1234ef56"

    assert _ref_tail(_B()) == "abcd1234"


def test_ref_tail_without_ref_is_stable() -> None:
    from veripen_cli.cmd_run import _ref_tail

    class _B:
        ref = ""

    assert _ref_tail(_B()) == "noref"

# ── B104：hunt 与 run 共用同一条强度规则 ─────────────────────────
def test_hunt_strength_matches_run_rule() -> None:
    from veripen_cli.cmd_hunt import _strength_of as hunt_strength

    assert hunt_strength(_contract_binding()) == "contract"
    assert hunt_strength(_crawl_binding()) == "crawl"
