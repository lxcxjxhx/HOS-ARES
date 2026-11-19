"""B517 · 17 条一致性自检逐条喂违规（B516 只验了第 3 条）。

判据：每条自检都要**抓到它应该抓的那条违规**，且编号不重复、gate 标记与文档一致。
这批用例只做「能红」，不重复验证「空库全绿」（`test_all_checks_react_to_a_broken_store` 已覆盖）。
"""

from __future__ import annotations

import pytest

from hos_ares_core.selfcheck import run_all
from hos_ares_core.store import Store


@pytest.fixture()
def store():
    s = Store(":memory:").init()
    try:
        yield s
    finally:
        s.close()


def _check(store: Store, no: int):
    return next(o for o in run_all(store) if o.no == no)


def _finding(store: Store, fid: str, **kw) -> None:
    row = {
        "id": fid, "task_id": "t-1", "asset_id": "a-1", "oracle_id": "idor_cross_account",
        "oracle_version": "1", "title": "标题", "severity": "low", "confidence": 0.9,
        "route_strength": None, "cwe": None, "state": "VERIFIED", "capsule_id": "cap-1",
        "created_at": "2026-11-20T00:00:00Z", "outcome": "reported",
        "coverage_src": "machine_observed", "not_run_reason": None, "error_reason": None,
    }
    row.update(kw)
    cols = ", ".join(row)
    ph = ", ".join("?" * len(row))
    store.conn.execute(f"INSERT INTO findings ({cols}) VALUES ({ph})", tuple(row.values()))


def _task(store: Store, tid: str = "t-1", **kw) -> None:
    row = {
        "id": tid, "engagement_id": "e-1", "idem_key": f"k-{tid}", "profile_name": "p",
        "profile_hash": "h", "asset_id": "a-1", "oracle_ids": "[]", "state": "PENDING",
        "lease_owner": None, "lease_until": 0, "heartbeat_at": 0, "attempt": 0,
        "max_attempts": 3, "error_class": None, "error_code": None, "budget_json": "{}",
        "routeset_sha256": None, "result_json": None,
        "created_at": "2026-11-20T00:00:00Z", "updated_at": "2026-11-20T00:00:00Z",
    }
    row.update(kw)
    cols = ", ".join(row)
    ph = ", ".join("?" * len(row))
    store.conn.execute(f"INSERT INTO tasks ({cols}) VALUES ({ph})", tuple(row.values()))


def _capsule(store: Store, cid: str, **kw) -> None:
    row = {"id": cid, "finding_id": "f-1", "path": f"c/{cid}.json", "self_sha256": "s",
           "prev_sha256": None, "routeset_sha256": "rs-1", "route_ref": "route://x",
           "sealed_at": "2026-11-20T00:00:00Z", "replay_status": "pending", "replay_at": None}
    row.update(kw)
    cols = ", ".join(row)
    ph = ", ".join("?" * len(row))
    store.conn.execute(f"INSERT INTO capsules ({cols}) VALUES ({ph})", tuple(row.values()))


# ── 逐条 ────────────────────────────────────────────────────────
def test_1_duplicate_idem_key_is_blocked_by_the_unique_constraint(store: Store) -> None:
    """检查 1 的判据由**数据库 UNIQUE 约束**保证——所以这条自检永远不会触发。

    这不是缺陷，是双保险：DB 拦在前面，自检只是事后核对。记录它，
    免得后来的人以为「永远 0 命中」等于「没实现」。
    """
    import sqlite3

    _task(store, "t-1", idem_key="dup")
    with pytest.raises(sqlite3.IntegrityError):
        _task(store, "t-2", idem_key="dup")
    assert _check(store, 1).violations == ()


def test_2_stuck_task(store: Store) -> None:
    _task(store, "t-stuck", state="LEASED", lease_until=1)
    assert _check(store, 2).violations


def test_3_verified_without_capsule(store: Store) -> None:
    _finding(store, "f-3", capsule_id=None)
    assert _check(store, 3).violations


def test_4_orphan_capsule(store: Store) -> None:
    _capsule(store, "cap-orphan", finding_id="no-such-finding")
    assert _check(store, 4).violations


def test_5_scope_violation_event(store: Store) -> None:
    store.conn.execute(
        "INSERT INTO task_events (task_id, ts, kind, ok, detail_json)"
        " VALUES ('t-1','2026-11-20T00:00:00Z','scope',0,'{\"VIOLATION\":1}')")
    assert _check(store, 5).violations


def test_6_capsule_without_route_evidence(store: Store) -> None:
    _capsule(store, "cap-6", routeset_sha256=None)
    assert _check(store, 6).violations


def test_7_dangling_routeset_reference(store: Store) -> None:
    _capsule(store, "cap-7", routeset_sha256="no-such-routeset")
    assert _check(store, 7).violations


def test_8_weak_binding_high_severity(store: Store) -> None:
    _finding(store, "f-8", route_strength="crawl", severity="high")
    assert _check(store, 8).violations


def test_9_rediscovery_over_quota(store: Store) -> None:
    _task(store, "t-9", budget_json='{"rediscovery":{"used":3,"max":1}}')
    assert _check(store, 9).violations


def test_10_expired_routeset_task_running(store: Store) -> None:
    store.conn.execute(
        "INSERT INTO routesets (sha256, asset_id, profile_hash, resolver_version, query_hash,"
        " sources_used, binding_count, path, created_at, expires_at)"
        " VALUES ('rs-old','a-1','p','1','q','contract',1,'p',0,1)")
    _task(store, "t-10", state="RUNNING", routeset_sha256="rs-old")
    assert _check(store, 10).violations


def test_11_unbound_ref_event(store: Store) -> None:
    store.conn.execute(
        "INSERT INTO task_events (task_id, ts, kind, ok, detail_json)"
        " VALUES ('t-1','2026-11-20T00:00:00Z','binding',0,'{\"UNBOUND\":1}')")
    assert _check(store, 11).violations


def test_12_fact_without_evidence(store: Store) -> None:
    store.conn.execute(
        "INSERT INTO blackboard_facts (id, task_id, subject, predicate, value, origin,"
        " from_target, evidence_id, ttl_s, created_at)"
        " VALUES ('f-12','t-1','s','p','v','agent',0,NULL,60,'2026-11-20T00:00:00Z')")
    assert _check(store, 12).violations


def test_13_target_data_unmarked(store: Store) -> None:
    store.conn.execute(
        "INSERT INTO blackboard_facts (id, task_id, subject, predicate, value, origin,"
        " from_target, evidence_id, ttl_s, created_at)"
        " VALUES ('f-13','t-1','s','p','v','target',0,'ev-1',60,'2026-11-20T00:00:00Z')")
    assert _check(store, 13).violations


def test_14_asset_fact_dangling_capsule(store: Store) -> None:
    store.conn.execute(
        "INSERT INTO asset_facts (id, asset_id, subject, predicate, value, origin,"
        " from_target, evidence_id, capsule_id, ttl_s, created_at)"
        " VALUES ('a-14','asset-1','s','p','v','target',1,'ev-1','no-capsule',60,"
        "'2026-11-20T00:00:00Z')")
    assert _check(store, 14).violations


def test_15_asset_fact_without_evidence(store: Store) -> None:
    store.conn.execute(
        "INSERT INTO asset_facts (id, asset_id, subject, predicate, value, origin,"
        " from_target, evidence_id, capsule_id, ttl_s, created_at)"
        " VALUES ('a-15','asset-1','s','p','v','target',1,'','cap-1',60,"
        "'2026-11-20T00:00:00Z')")
    assert _check(store, 15).violations


def test_16_crash_counted_as_no_finding(store: Store) -> None:
    _finding(store, "f-16", outcome="reported", state="BLOCKED")
    assert _check(store, 16).violations


def test_17_self_reported_claiming_coverage(store: Store) -> None:
    _finding(store, "f-17", coverage_src="machine_observed")
    assert _check(store, 17).violations
