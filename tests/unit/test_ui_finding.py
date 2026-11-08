"""B40 详情页五段（H06 §3 / H07 判据 A–D）。

详情页是唯一"点进去看证据"的入口，所以它必须做到：
A 任意 id 可取，取不到要说清楚（不冒充命中）；
B 地址与模板正文永不出现（只给 8 位 ref + 来源类别）；
C capsule 缺失必须显性（不可复现的结论不许看起来像可复现）；
D 缺字段给"未提供"而不是 undefined/空串。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from web import ConsoleState, Row, build_app

SLASH = chr(0x2F)
UI = SLASH + "ui" + SLASH


def _row(**kw) -> Row:
    base = dict(
        finding_id="F-001", title="xss 在 target 上成立", severity="medium",
        oracle_id="xss_reflected_ctx", asset_id="asset-a", confidence=1.0,
        route_strength="contract", route_ref="route://contract/7f3a2c11d9e04b58",
        replay_status="OK", template_sha256="b" * 64, capsule_id="sha256:abc", hits=1,
    )
    base.update(kw)
    return Row(**base)


def _client(state: ConsoleState | None = None) -> TestClient:
    return TestClient(build_app(state or ConsoleState(findings=[_row()])))


def _data(resp) -> dict:  # noqa: ANN001
    return resp.json()["data"]


# ── A 任意 id 可取 ─────────────────────────────────────────────
def test_finding_any_id_and_explicit_fallback() -> None:
    rows = [_row(), _row(finding_id="F-002", oracle_id="sqli_bool_diff")]
    client = TestClient(build_app(ConsoleState(findings=rows)))
    assert _data(client.get(UI + "finding?id=F-002"))["id"] == "F-002"
    fallback = _data(client.get(UI + "finding?id=ghost"))
    assert fallback["selected_by"] == "fallback" and fallback["requested"] == "ghost"


def test_finding_with_no_rows_is_empty_payload() -> None:
    assert _data(_client(ConsoleState()).get(UI + "finding")) == {}


# ── B 地址与模板永不出现 ───────────────────────────────────────
def test_finding_never_exposes_full_ref_or_scheme() -> None:
    body = _client().get(UI + "finding?id=F-001").text
    data = json.loads(body)["data"]
    assert len(data["route"]) == 8, "只给前 8 位"
    assert "route://" not in body and "http://" not in body
    assert data["bound"] is True


def test_finding_route_source_is_a_category_not_a_template() -> None:
    data = _data(_client().get(UI + "finding"))
    assert data["route_source"] == "contract"
    assert data["route"].isalnum(), "ref 前缀只能是十六进制哈希片段"


# ── C capsule 缺失必须显性 ────────────────────────────────────
def test_finding_capsule_absence_is_explicit() -> None:
    assert _data(_client().get(UI + "finding"))["capsule_present"] is True
    blocked = _row(finding_id="F-009", outcome="blocked", reason_code="BLOCKED: x",
                   capsule_id=None, severity=None)
    data = _data(TestClient(build_app(ConsoleState(findings=[blocked]))).get(UI + "finding?id=F-009"))
    assert data["capsule_present"] is False and data["capsule"] is None
    assert data["assertion"]["severity"] is None, "未验证不得带分档"


# ── D 缺字段给"未提供"（契约里必须是 None/[] 而不是省略）──────
def test_finding_missing_pieces_are_present_but_empty() -> None:
    data = _data(_client().get(UI + "finding"))
    assert data["traces"] == [], "没有 trace 就给空列表，前端显示「未提供」"
    # B157：没有证据文件就不给复现命令。旧契约的 `make replay ID=<finding_id>`
    # 把 finding_id 当 capsule 路径，拷出去必然"载入失败"——给了等于没给。
    assert data["capsule_path"] is None
    assert data["repro"] == ""
    assert data["repro_ok"] is False


def test_finding_injects_traces_from_state() -> None:
    traces = [{"role": "baseline", "status": 200, "bytes": 12},
              {"role": "probe", "status": 200, "bytes": 13},
              {"role": "control", "status": 404, "bytes": 9}]
    client = TestClient(build_app(ConsoleState(findings=[_row()], traces={"F-001": traces})))
    data = _data(client.get(UI + "finding?id=F-001"))
    assert [t["role"] for t in data["traces"]] == ["baseline", "probe", "control"]


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_finding_stays_read_only(method: str) -> None:
    assert getattr(_client(), method)(UI + "finding").status_code == 405
