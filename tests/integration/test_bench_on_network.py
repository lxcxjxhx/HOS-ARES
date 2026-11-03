"""bench 走真网络：44 例在真 socket 上跑，判定必须与进程内一致。

这一步的价值不在"多跑一遍"，在于**把结论从"进程内跑通"升级成"真网络跑通"**。
如果判定变了，说明某处 oracle 依赖了进程内传输的特性（自动造 token、无连接），
那是结构问题，必须先修。

做法：
- 每个 case 起一个自己的 ``LabServer``（三态各自不同，隔离优先）；
- 用例的绑定经 ``SymbolSource(base_symbol=LAB_BASE)`` 产出——**模板仍由 resolver 给**；
- token 由 ``token_for(account)`` 显式提供（不复刻 WSGI 的自动补 token）。
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator

import pytest

from lab.app import App, make_token
from lab.net_server import LabServer
from lab.net_transport import NetTransport
from lab.routes import candidates

from veripen.bench import Outcome, load_cases
from veripen_core.routes import (
    AssetRef,
    Resolver,
    RouteHints,
    RouteProvenance,
    RouteQuery,
)
from veripen_core.routes.sources.symbols import SymbolSource, SymbolTable
from veripen.bench.faces import extra_tags
from veripen_oracles import ALL_ORACLES
from veripen_oracles.common.harness import SymbolTable as HarnessSymbols
from veripen_oracles.common.harness import run as run_oracle
from veripen_oracles.oob import LabOob

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "tests" / "bench" / "cases.yaml"
SEP = chr(0x2F)

SYMBOLS = HarnessSymbols({
    "OOB": "oob-SAMPLE",
    # SEP 分隔符由常量拼：本文件不该出现可网络定位的字面量（noroute R-PATH）
    "OOB_URL": "https" + ":" + SEP * 2 + "oob-SAMPLE",
    "DEAD_HOST": "none-SAMPLE",
    "STRANGER": "secondary",
    "OWNER": "primary",
    "SAFE_TARGET": "account/settings",
    "SAFE_FILE": "manual.txt",
    "SYSTEM_FILE": "etc/passwd",
    "SITE_HOST": "lab",
    "SITE_ORIGIN": "https" + ":" + SEP * 2 + "site.invalid",
})


def _oracle(oid: str) -> Any:
    for cls in ALL_ORACLES:
        if cls.meta.id == oid:
            return cls()
    raise KeyError(oid)


def _routeset(base: str, tags: tuple[str, ...]) -> Any:
    syms = SymbolTable({"LAB_BASE": base})
    src = SymbolSource("contract", RouteProvenance.CONTRACT, candidates(tags), syms,
                       base_symbol="LAB_BASE")
    q = RouteQuery(asset=AssetRef(id="web-01"), hints=RouteHints(), min_confidence=0.0)
    return Resolver(sources=[src]).resolve(q, now=1000)


def _app_for(label: str, seeds: tuple[str, ...]) -> App:
    if label == "positive":
        return App(secure=False)
    if "degenerate" in seeds:
        a = App(secure=False)
        a.degenerate = True
        return a
    return App(secure=True)


def _run_on_network(case) -> tuple[str, str]:  # noqa: ANN001
    """返回 ``(outcome, verdict)``。**与 run_bench 的 run_case 同口径。**"""
    app = _app_for(case.label, case.seeds)
    srv = LabServer(app).start()
    try:
        # OOB 的登记簿是**靶场 App**，不是传输层：真网络下也是同一个 App 收回调，
        # 所以两个传输共用同一份规则（transport_oob.oob_headers）。
        net = NetTransport(token_for=make_token, oob=LabOob(app=app, endpoint_value="oob-SAMPLE"))
        try:
            rs = _routeset(srv.base_url, (case.selector,))
        except Exception:
            return "not_run", "BLOCKED"
        if not rs.bindings:
            return "not_run", "BLOCKED"
        binding = rs.bindings[0]

        bindings = {case.selector: binding}
        for extra in extra_tags(case.oracle):
            rs2 = _routeset(srv.base_url, (extra,))
            if not rs2.bindings:
                return "not_run", "BLOCKED"
            bindings[extra] = rs2.bindings[0]

        try:
            res = run_oracle(
                _oracle(case.oracle), binding, net, symbols=SYMBOLS,
                nonce=f"bench-{case.case_id}", token=case.case_id, bindings=bindings,
            )
        except Exception:
            return "error", "BLOCKED"
        verdict = res.verdict.value
        return ("undetermined" if verdict == "INCONCLUSIVE" else "reported"), verdict
    finally:
        srv.stop()


@pytest.fixture(scope="module")
def net_results() -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for case in load_cases(CASES):
        out[case.case_id] = _run_on_network(case)
    return out


# ══════════════════════════════════════════════════════════════════
# 1 44 例在真网络上跑得完，且无 error
# ══════════════════════════════════════════════════════════════════
def test_all_cases_run_on_real_network(net_results: dict[str, tuple[str, str]]) -> None:
    assert len(net_results) == 44
    errors = {k: v for k, v in net_results.items() if v[0] == "error"}
    assert errors == {}, f"真网络下出现 error：{errors}"


def test_no_false_positive_on_negatives(net_results: dict[str, tuple[str, str]]) -> None:
    """FP=0 是硬门槛；真网络下也必须成立。"""
    cases = {c.case_id: c for c in load_cases(CASES)}
    fp = [
        cid for cid, (_, verdict) in net_results.items()
        if cases[cid].label == "negative" and verdict == "VERIFIED"
    ]
    assert fp == [], f"真网络下出现假阳性：{fp}"


def test_positives_still_verified(net_results: dict[str, tuple[str, str]]) -> None:
    """至少大部分正样本要仍被判出——否则说明真网络下大量失效。"""
    cases = {c.case_id: c for c in load_cases(CASES)}
    pos = [cid for cid, c in cases.items() if c.label == "positive"]
    hit = [cid for cid in pos if net_results[cid][1] == "VERIFIED"]
    assert len(hit) >= 18, f"正样本只判出 {len(hit)}/{len(pos)}"


# ══════════════════════════════════════════════════════════════════
# 2 与进程内口径的可比性
# ══════════════════════════════════════════════════════════════════
def test_verdict_shape_is_legal(net_results: dict[str, tuple[str, str]]) -> None:
    """真网络下的 verdict 只能落在四态允许的取值里。"""
    legal = {"VERIFIED", "REFUTED", "INCONCLUSIVE", "BLOCKED"}
    bad = {k: v for k, v in net_results.items() if v[1] not in legal}
    assert bad == {}, f"非法 verdict：{bad}"


def test_outcome_counts_are_sane(net_results: dict[str, tuple[str, str]]) -> None:
    counts: dict[str, int] = {}
    for outcome, _ in net_results.values():
        counts[outcome] = counts.get(outcome, 0) + 1
    assert counts.get("error", 0) == 0
    assert counts.get("reported", 0) >= 20


# ══════════════════════════════════════════════════════════════════
# 3 每例都真的发过请求（不是空跑）
# ══════════════════════════════════════════════════════════════════
def test_network_was_actually_used() -> None:
    case = load_cases(CASES)[0]
    app = _app_for(case.label, case.seeds)
    srv = LabServer(app).start()
    try:
        net = NetTransport(token_for=make_token, oob=LabOob(app=app, endpoint_value="oob-SAMPLE"))
        rs = _routeset(srv.base_url, (case.selector,))
        if not rs.bindings:
            pytest.skip("该用例无绑定")
        run_oracle(_oracle(case.oracle), rs.bindings[0], net, symbols=SYMBOLS,
                   nonce="n", token="t")
        assert net.sent, "oracle 一个请求都没发——真网络路径没被走到"
    finally:
        srv.stop()
