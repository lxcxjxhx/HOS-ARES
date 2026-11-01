"""B15 控制台测试（22 §2–§3 / 24 §2.6）。

四条硬约束逐条验：
1. `ui.*` 写方法 → 405
2. 绑定页只显示摘要，**永不显示模板正文**
3. 降噪排序用公式，不是「看起来严重的先」
4. 越界非空 → 页头红色；未验证按 outcome 分组
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

SLASH = chr(0x2F)
UI = SLASH + "ui" + SLASH
TPL = SLASH + "api" + SLASH + "items" + SLASH + "{id}"

from web import (
    UI_OPS,
    ConsoleState,
    Queue,
    Row,
    bounds_view,
    build_app,
    coverage_view,
    findings_view,
    order_rows,
    rollup,
    routes_view,
    unverified_view,
)


def _row(**kw) -> Row:  # noqa: ANN003
    base = dict(
        finding_id="f-1",
        title="对象级授权缺失",
        severity="high",
        oracle_id="idor_cross_account",
        asset_id="web-01",
        confidence=0.95,
        route_strength="contract",
        route_ref="route://contract/7f3a2c11d9e04b58",
        replay_status="OK",
        template_sha256="b" * 64,
        capsule_id="c" * 64,
    )
    base.update(kw)
    return Row(**base)


def _client(state: ConsoleState | None = None) -> TestClient:
    return TestClient(build_app(state or ConsoleState(findings=[_row()])))


# ── 1 只读：写方法一律 405 ────────────────────────────────────────
@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_write_methods_are_405(method: str) -> None:
    client = _client()
    response = getattr(client, method)(UI + "findings")
    assert response.status_code == 405
    assert response.json()["detail"]["reason"] == "console is read-only"


def test_all_ui_ops_are_gettable() -> None:
    client = _client()
    for op in UI_OPS:
        path = UI + op.split(".", 1)[-1]
        assert client.get(path).status_code == 200, op


def test_unknown_ui_path_is_404() -> None:
    assert _client().get(UI + "nope").status_code == 404


def test_op_id_roundtrip_in_payload() -> None:
    body = _client().get(UI + "findings").json()
    assert body["op"] == "ui.findings"


# ── 2 绑定页：只显示摘要 ──────────────────────────────────────────
def test_routes_view_hides_templates() -> None:
    bindings = [
        {"ref": "route://contract/7f3a2c11d9e04b58", "semantic_tags": ["object_read"],
         "source": "contract", "confidence": 0.9, "ttl_s": 900,
         "template": TPL, "template_sha256": "b" * 64},
    ]
    view = routes_view(bindings)
    text = str(view)
    assert TPL not in text and (SLASH + "api") not in text, "绑定页永不显示模板正文"
    assert view["template_visible"] is False
    assert view["exportable"] is False
    assert view["by_tag"] == {"object_read": 1}


# ── 3 降噪排序（22 §3.1 公式）─────────────────────────────────────
def test_priority_formula() -> None:
    strong_high = _row(severity="high", route_strength="contract", replay_status="OK")
    weak_low = _row(severity="low", route_strength="probe", replay_status="NEVER")
    assert strong_high.priority == 3 * 3 + 2 * 1 + 1 * 1
    assert weak_low.priority == 3 * 1 + 2 * 0 + 0


def test_new_round_adds_one() -> None:
    base = _row()
    assert _row(is_new=True).priority == base.priority + 1


def test_silenced_drops_three() -> None:
    base = _row()
    assert _row(silenced=True).priority == base.priority - 3
    assert _row(silenced=True).queue is Queue.SILENCED


def test_queue_buckets() -> None:
    assert _row(severity="critical").queue is Queue.NOW
    assert _row(severity="high").queue is Queue.NOW
    assert _row(severity="medium").queue is Queue.WEEK
    assert _row(severity="info", route_strength="probe").queue is Queue.LOW


def test_order_uses_formula_then_stable_tiebreak() -> None:
    rows = [
        _row(finding_id="f-c", severity="info", oracle_id="x", template_sha256="z" * 64),
        _row(finding_id="f-a", severity="critical", oracle_id="a", template_sha256="a" * 64),
        _row(finding_id="f-b", severity="critical", oracle_id="b", template_sha256="b" * 64),
    ]
    ordered = order_rows(rows)
    assert ordered[0].finding_id == "f-a"
    assert ordered[1].finding_id == "f-b", "同分按 oracle_id 字典序，不按 ulid"
    assert ordered[-1].finding_id == "f-c"


def test_rollup_merges_and_marks() -> None:
    a = _row(finding_id="f-1")
    b = _row(finding_id="f-2")
    merged = rollup([a, b])
    heads = [r for r in merged if not r.rolled_up]
    assert len(heads) == 1 and heads[0].hits == 2


def test_findings_view_collapses_low_by_default() -> None:
    rows = [_row(), _row(finding_id="f-low", severity="info", route_strength="probe",
                         oracle_id="sqli", template_sha256="z" * 64)]
    view = findings_view(rows)
    assert view["collapsed_low"] == 1
    assert all(r["queue"] != "low" for r in view["rows"])
    expanded = findings_view(rows, include_low=True)
    assert any(r["queue"] == "low" for r in expanded["rows"])


def test_findings_view_route_is_short() -> None:
    view = findings_view([_row()])
    assert len(view["rows"][0]["route"]) == 8


# ── 4 越界页 / 未验证分组 / 覆盖矩阵 ─────────────────────────────
def test_bounds_page_turns_red_when_nonempty() -> None:
    assert bounds_view([])["alert"] is None
    view = bounds_view([{"ip": "203.0.113.9", "reason": "peer_in_deny"}])
    assert view["alert"] == "red" and view["count"] == 1


def test_unverified_groups_by_outcome() -> None:
    rows = [
        _row(finding_id="f-2", outcome="undetermined", reason_code="INCONCLUSIVE"),
        _row(finding_id="f-3", outcome="error", reason_code="NETWORK_ERROR"),
        _row(finding_id="f-4", outcome="not_run", reason_code="SKIPPED"),
        _row(finding_id="f-5", outcome="reported"),
    ]
    view = unverified_view(rows)
    assert view["counts"] == {"undetermined": 1, "error": 1, "not_run": 1}


def test_coverage_matrix_marks_untested() -> None:
    view = coverage_view(
        assets=["web-01"], oracles=["sqli", "idor"],
        tested={("web-01", "sqli"): "verified"}, zero_hit_tags=["graphql_endpoint"],
    )
    cells = view["matrix"][0]["cells"]
    assert cells["sqli"] == "verified" and cells["idor"] == "not_run"
    assert view["zero_hit_tags"] == ["graphql_endpoint"]


# ── 5 路由由 op 表派生（可替换）──────────────────────────────────
def test_paths_can_be_rebound_without_code_change() -> None:
    rebound = SLASH + "console" + SLASH + "list"
    app = build_app(ConsoleState(findings=[_row()]), op_paths={"ui.findings": rebound})
    client = TestClient(app)
    assert client.get(rebound).status_code == 200
    assert client.post(rebound).status_code == 405


def test_no_hardcoded_paths_in_console_source() -> None:
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "web" / "console.py"
    text = src.read_text(encoding="utf-8")
    for name in ("overview", "findings"):
        for quote in ("'", '"'):
            assert quote + UI + name + quote not in text, "页面路径必须由 op 表派生"


# ── 6 单页静态入口（22 §2 技术选型）────────────────────────────────
def test_static_page_is_served() -> None:
    client = _client()
    res = client.get(UI + "app")
    assert res.status_code == 200
    assert "VeriPen" in res.text
    assert "只读" in res.text


def test_static_page_does_not_hardcode_api_paths() -> None:
    """前端页面清单由后端 op 表给；页面里出现的只是相对拼接。"""
    from pathlib import Path

    page = Path(__file__).resolve().parents[2] / "web" / "static" / "index.html"
    text = page.read_text(encoding="utf-8")
    assert (SLASH + "api") not in text
    assert "fetch(" in text
