"""B64 · ui.audit 接 task_events（H11 判据 A–E）。

原来 audit 页恒空，因为 `task_events` 没有写入方（H09 §4 开放问题 #2）。
B60 补上写入方，这里验读侧：有源、脱敏、空库显式。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from veripen_core.audit_source import AUDIT_KEYS, load_audit, to_row
from veripen_core.blackboard import EventLog
from veripen_core.store import Store
from web.console import ConsoleState, build_app

#: 路径字面量一律用 chr 拼（项目既有做法，`noroute` 禁止测试里写裸地址）
SLASH = chr(0x2F)
UI = SLASH + "ui" + SLASH


def _store() -> Store:
    return Store(":memory:").init()


def _seed(store: Store, n: int = 3) -> None:
    log = EventLog(store)
    for i in range(n):
        log.record(
            task_id="T-1", kind="oracle", ok=(i % 2 == 0),
            oracle_id=f"oracle-{i}", route_ref=f"{SLASH}api{SLASH}v{i}",
            detail={"payload": "SECRET-MARKER", "n": i},
        )


def _client(audit: list[dict[str, object]]) -> TestClient:
    return TestClient(build_app(ConsoleState(findings=[], audit=audit)))


# ── A：有源 ────────────────────────────────────────────────────
def test_audit_reads_store_and_reports_state() -> None:
    store = _store()
    _seed(store, 3)
    rows = load_audit(store)
    assert len(rows) == 3

    data = _client(rows).get(UI + "audit").json()["data"]
    assert data["source"] == "state"
    assert data["count"] == 3


def test_order_is_chronological() -> None:
    store = _store()
    _seed(store, 4)
    seqs = [r["seq"] for r in load_audit(store)]
    assert seqs == sorted(seqs), "最近的应排在最后"


def test_limit_keeps_most_recent() -> None:
    store = _store()
    _seed(store, 10)
    rows = load_audit(store, limit=3)
    assert len(rows) == 3
    assert rows[-1]["seq"] == 10, "取最近的 N 条，然后翻回正序"


# ── B：空库显式 ────────────────────────────────────────────────
def test_empty_store_reports_unavailable() -> None:
    store = _store()
    assert load_audit(store) == []
    data = _client(load_audit(store)).get(UI + "audit").json()["data"]
    assert data["source"] == "unavailable"
    assert data["count"] == 0


# ── C：六键脱敏 ────────────────────────────────────────────────
def test_detail_not_passed_through() -> None:
    store = _store()
    _seed(store, 1)
    row = load_audit(store)[0]
    assert set(row) == set(AUDIT_KEYS), f"多余字段会带出 payload：{set(row) - set(AUDIT_KEYS)}"
    assert "SECRET-MARKER" not in str(row)


def test_malformed_detail_does_not_crash() -> None:
    row = to_row({"seq": 1, "ts": 0, "kind": "oracle", "ok": 1,
                  "detail_json": "{not json", "route_ref": SLASH + "x"})
    assert set(row) == set(AUDIT_KEYS)
    assert row["what"] == "oracle"


# ── D：审批四态可读 ────────────────────────────────────────────
def test_approval_events_carry_who_and_state() -> None:
    from veripen_core.guard.approval import ApprovalGate, ApprovalRequired

    store = _store()
    gate = ApprovalGate.from_spec(
        [{"action_class": "scope_expand"}],
        store=store, events=EventLog(store), task_id="T-1",
    )
    with pytest.raises(ApprovalRequired):
        gate.require("scope_expand", now=1000, impact={"target": "api.test"})
    gate.decide("scope_expand", approved=True, by="human", lease_owner="agent", now=1010)

    rows = load_audit(store)
    assert len(rows) == 2
    decision = rows[-1]
    assert decision["who"] == "human"
    assert decision["what"] == "approval:decision"
    assert decision["asset"] == "scope_expand"
    assert decision["ok"] is True


# ── E：只读不变 ────────────────────────────────────────────────
def test_audit_page_still_rejects_writes() -> None:
    store = _store()
    _seed(store, 1)
    client = _client(load_audit(store))
    for method in ("POST", "PUT", "DELETE"):
        resp = client.request(method, UI + "audit", json={})
        assert resp.status_code == 405, f"{method} 应被拒，实际 {resp.status_code}"
