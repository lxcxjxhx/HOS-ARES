"""B60 · 审批落库与事件（H11 判据 A–E）。

五个断言对应 H11 的 B60 判据：
A 落库 / B 事件 / C 自批留痕 / D 过期留痕 / E 只增不改。
"""

from __future__ import annotations

import pytest

from veripen_core.blackboard import EventLog
from veripen_core.guard.approval import (
    ApprovalGate,
    ApprovalRequired,
    ApprovalState,
    ApprovalTTLExpired,
)
from veripen_core.store import Store


def _gate(*, ttl_s: int = 3600) -> tuple[ApprovalGate, Store]:
    store = Store(":memory:").init()
    gate = ApprovalGate.from_spec(
        [{"action_class": "scope_expand"}],
        ttl_s=ttl_s,
        store=store,
        events=EventLog(store),
        task_id="T-001",
    )
    return gate, store


def _rows(store: Store, sql: str, *args: object) -> list[object]:
    return store.conn.execute(sql, args).fetchall()


def _events(store: Store) -> list[object]:
    return _rows(store, "SELECT * FROM task_events WHERE kind='approval' ORDER BY seq")


# ── A：落库 ─────────────────────────────────────────────────────
def test_request_persists_pending_row() -> None:
    gate, store = _gate()
    with pytest.raises(ApprovalRequired):
        gate.require("scope_expand", now=1000, impact={"target": "T"})

    rows = _rows(store, "SELECT id, state, requested_at, task_id FROM approvals")
    assert len(rows) == 1
    assert rows[0]["state"] == "PENDING"
    assert rows[0]["requested_at"] == 1000
    assert rows[0]["task_id"] == "T-001"
    # 内存 record 与库内 id 是同一个
    assert rows[0]["id"] == gate.decisions[-1].id


def test_decision_updates_same_row() -> None:
    gate, store = _gate()
    with pytest.raises(ApprovalRequired):
        gate.require("scope_expand", now=1000)
    gate.decide("scope_expand", approved=True, by="human", lease_owner="agent", now=1010)

    rows = _rows(store, "SELECT state, decided_by, decided_at, reason FROM approvals")
    assert len(rows) == 1, "决策必须更新同一行，不是插新行"
    assert rows[0]["state"] == "APPROVED"
    assert rows[0]["decided_by"] == "human"
    assert rows[0]["decided_at"] == 1010


# ── B：事件 ─────────────────────────────────────────────────────
def test_each_phase_appends_one_event() -> None:
    gate, store = _gate()
    with pytest.raises(ApprovalRequired):
        gate.require("scope_expand", now=1000)
    gate.decide("scope_expand", approved=True, by="human", lease_owner="agent", now=1010)

    evs = _events(store)
    assert [e["kind"] for e in evs] == ["approval", "approval"]
    assert len({e["detail_json"] for e in evs}) == 2, "两条事件的 detail 必须不同（request vs decision）"
    assert '"phase": "request"' in evs[0]["detail_json"]
    assert '"phase": "decision"' in evs[1]["detail_json"]
    assert evs[0]["ok"] == 0 and evs[1]["ok"] == 1


def test_event_seq_is_monotonic() -> None:
    gate, store = _gate()
    with pytest.raises(ApprovalRequired):
        gate.require("scope_expand", now=1000)
    gate.decide("scope_expand", approved=False, by="human", lease_owner="agent", now=1010)

    seqs = [int(e["seq"]) for e in _events(store)]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


# ── C：自批 ─────────────────────────────────────────────────────
def test_self_approval_denied_and_recorded() -> None:
    gate, store = _gate()
    with pytest.raises(ApprovalRequired):
        gate.require("scope_expand", now=1000)

    with pytest.raises(PermissionError):
        gate.decide("scope_expand", approved=True, by="agent", lease_owner="agent", now=1010)

    evs = _events(store)
    assert len(evs) == 2, "自批被拒也必须留痕"
    assert "self_approval" in evs[-1]["detail_json"]
    assert evs[-1]["ok"] == 0
    # 内存态也必须是 REJECTED，不能停在 PENDING 让人重试
    assert gate.decisions[-1].state is ApprovalState.REJECTED


# ── D：过期 ─────────────────────────────────────────────────────
def test_expired_marked_and_recorded() -> None:
    gate, store = _gate(ttl_s=60)
    with pytest.raises(ApprovalRequired):
        gate.require("scope_expand", now=1000)

    with pytest.raises(ApprovalTTLExpired):
        gate.decide("scope_expand", approved=True, by="human", lease_owner="agent", now=2000)

    rows = _rows(store, "SELECT state, decided_at FROM approvals")
    assert rows[0]["state"] == "EXPIRED"
    assert rows[0]["decided_at"] == 2000

    evs = _events(store)
    assert len(evs) == 2
    assert '"expired": true' in evs[-1]["detail_json"]


# ── E：只增不改 + 旧行为不变 ────────────────────────────────────
def test_memory_only_mode_unchanged() -> None:
    """不给 store/events 时行为与 B54 完全一致：纯内存，不抛额外异常。"""
    gate = ApprovalGate.from_spec([{"action_class": "scope_expand"}])
    with pytest.raises(ApprovalRequired):
        gate.require("scope_expand", now=1000)
    rec = gate.decide("scope_expand", approved=True, by="human", lease_owner="agent", now=1010)
    assert rec.state is ApprovalState.APPROVED
    assert rec.id, "内存模式也要有 id，否则跨模式日志对不上"


def test_no_update_path_on_events() -> None:
    """task_events 只有 INSERT：跑完整流程后库里不存在改动痕迹（seq 连续且总数匹配）。"""
    gate, store = _gate()
    with pytest.raises(ApprovalRequired):
        gate.require("scope_expand", now=1000)
    gate.decide("scope_expand", approved=True, by="human", lease_owner="agent", now=1010)

    total = _rows(store, "SELECT COUNT(*) AS n FROM task_events")[0]["n"]
    approvals = _rows(store, "SELECT COUNT(*) AS n FROM task_events WHERE kind='approval'")[0]["n"]
    assert total == approvals == 2
