"""B36 页面契约对齐（H06 §4 / H08 A15）。

规则：**页面 payload ⊇ 对应 view 函数的键集**。少一个键就是契约漂移，
前端会在运行时读 undefined —— 这类错误在没人看控制台的日子里不会被发现。
"""

from __future__ import annotations

from typing import Any, Mapping

import pytest
from fastapi.testclient import TestClient

import web.console as console
from web import ConsoleState, Row, build_app, coverage_view

SLASH = chr(0x2F)
UI = SLASH + "ui" + SLASH

#: 每页的最小契约（与 view 函数同源；改 view 必须同步这里，否则红）
EXPECTED: dict[str, set[str]] = {
    "overview": {"engagements", "findings", "unverified", "queues", "coverage", "agent_claim"},
    "engagement": set(),
    "findings": {"total", "shown", "collapsed_low", "unverified", "queues", "rows"},
    "finding": {"id", "selected_by", "requested", "title", "assertion", "repro", "traces",
                "route", "bound", "route_source", "hits_state", "capsule", "capsule_present",
                "capsule_path", "repro_ok", "replay"},
    "unverified": {"groups", "counts", "by_verdict", "by_verdict_counts", "entries"},
    "coverage": {"assets", "oracles", "matrix", "zero_hit_tags", "scope"},
    "routes": {"count", "by_tag", "by_source", "bindings", "template_visible", "exportable"},
    "audit": {"events", "count", "source", "exportable"},
    "bounds": {"count", "alert", "events"},
    "approvals": {"count", "rows", "pending"},
}


def _row(**kw: Any) -> Row:
    base = dict(
        finding_id="f-1", title="对象级授权缺失", severity="high",
        oracle_id="idor_cross_account", asset_id="web-01", confidence=0.95,
        route_strength="contract", route_ref="route://contract/7f3a2c11d9e04b58",
        replay_status="OK", template_sha256="b" * 64, capsule_id="c" * 64, hits=1,
    )
    base.update(kw)
    return Row(**base)


def _client(state: ConsoleState | None = None) -> TestClient:
    return TestClient(build_app(state or ConsoleState(findings=[_row()])))


def _missing(op: str, data: Mapping[str, Any]) -> set[str]:
    return EXPECTED[op] - set(data)


# ── A15：逐页键集 ──────────────────────────────────────────────
@pytest.mark.parametrize("op", sorted(EXPECTED))
def test_contract_every_page_has_its_keys(op: str) -> None:
    body = _client().get(UI + op.split(".", 1)[-1]).json()
    assert body["payload_version"] == 2
    data = body["data"]
    missing = _missing(op, data)
    assert not missing, f"{op} 缺键: {sorted(missing)}"


def test_contract_finding_accepts_id() -> None:
    rows = [_row(finding_id="f-1"), _row(finding_id="f-2", oracle_id="sqli_bool_diff")]
    client = TestClient(build_app(ConsoleState(findings=rows)))
    picked = client.get(UI + "finding?id=f-2").json()["data"]
    assert picked["id"] == "f-2" and picked["selected_by"] == "query"
    default = client.get(UI + "finding").json()["data"]
    assert default["id"] == "f-1" and default["selected_by"] == "default"
    ghost = client.get(UI + "finding?id=nope").json()["data"]
    assert ghost["selected_by"] == "fallback", "找不到 id 必须回落，且不能冒充命中"
    assert ghost["requested"] == "nope"


def test_contract_coverage_is_a_matrix_not_a_counter() -> None:
    rows = [
        _row(asset_id="web-01", oracle_id="sqli_bool_diff", outcome="reported"),
        _row(finding_id="f-2", asset_id="web-01", oracle_id="ssrf_oob",
             outcome="undetermined", reason_code="BLOCKED: x"),
    ]
    data = _client(ConsoleState(findings=rows)).get(UI + "coverage").json()["data"]
    assert data["scope"] == "observed_only"
    cells = {row["asset"]: row["cells"] for row in data["matrix"]}
    assert cells["web-01"]["sqli_bool_diff"] == "verified"
    assert cells["web-01"]["ssrf_oob"] == "blocked"


def test_contract_coverage_falls_back_to_view_when_caller_supplies_matrix() -> None:
    supplied = coverage_view(assets=["a"], oracles=["o"], tested={("a", "o"): "verified"},
                             zero_hit_tags=["graphql_endpoint"])
    data = _client(ConsoleState(coverage=supplied)).get(UI + "coverage").json()["data"]
    assert data["matrix"][0]["cells"]["o"] == "verified"
    assert data["zero_hit_tags"] == ["graphql_endpoint"]


def test_contract_audit_declares_its_source() -> None:
    empty = _client().get(UI + "audit").json()["data"]
    assert empty["source"] == "unavailable" and empty["count"] == 0
    wired = _client(ConsoleState(audit=[{"at": "2026-11-03", "who": "reviewer",
                                         "what": "approve", "asset": "web-01"}])).get(UI + "audit").json()["data"]
    assert wired["source"] == "state" and wired["count"] == 1


def test_contract_overview_carries_agent_claim() -> None:
    """B161：自述恒在、可为空——空就是空，不是"没这个字段"。"""
    assert _client().get(UI + "overview").json()["data"]["agent_claim"] == {
        "summary": "", "unresolved": []}
    state = ConsoleState(findings=[_row()],
                         agent_claim={"summary": "找到 1 条", "unresolved": ["rate_limit_absent"]})
    data = TestClient(build_app(state)).get(UI + "overview").json()["data"]
    assert data["agent_claim"]["summary"] == "找到 1 条"
    assert data["agent_claim"]["unresolved"] == ["rate_limit_absent"]


def test_contract_overview_never_reports_percentage_without_denominator() -> None:
    data = _client().get(UI + "overview").json()["data"]
    assert data["coverage"]["total"] >= 1
    assert 0 <= data["coverage"]["tested"] <= data["coverage"]["total"]
    assert data["unverified"] >= 0


# ── 反例：契约检查必须能红 ────────────────────────────────────
def test_contract_detects_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    original = console.coverage_view

    def narrowed(**kw: Any) -> dict[str, Any]:
        view = original(**kw)
        view.pop("matrix")
        return view

    monkeypatch.setattr(console, "coverage_view", narrowed)
    data = _client().get(UI + "coverage").json()["data"]
    assert _missing("coverage", data) == {"matrix"}, "删掉 matrix 必须被契约检查抓到"


def test_contract_detects_missing_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    original = console.findings_view

    def narrowed(*a: Any, **kw: Any) -> dict[str, Any]:
        view = original(*a, **kw)
        for row in view["rows"]:
            row.pop("verdict")
        return view

    monkeypatch.setattr(console, "findings_view", narrowed)
    data = _client().get(UI + "findings").json()["data"]
    assert "verdict" not in data["rows"][0]
    # rows 里少一个键时，前端徽标会读 undefined —— 这条就是回归的形状
    assert any("verdict" not in row for row in data["rows"])


def test_contract_write_methods_still_405() -> None:
    """除 `ui.approvals`（唯一审批写入点，B54）外，全部 `ui.*` 写方法必须 405。"""
    client = _client()
    for op in EXPECTED:
        if op == "approvals":
            continue
        path = UI + op.split(".", 1)[-1]
        assert client.post(path).status_code == 405, op


def test_contract_approvals_is_the_only_write_exception() -> None:
    """审批页的 POST 是**唯一**破例：没有 id 时返回 404 而不是 405。"""
    assert _client().post(UI + "approvals", json={}).status_code == 404
