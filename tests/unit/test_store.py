"""存储层与状态机测试（`03 §2`/§3 的机械验证）。"""

from __future__ import annotations

import pytest

from veripen_core import (
    ALLOWED_TRANSITIONS,
    Budget,
    Store,
    Task,
    TaskState,
    TransitionError,
    idem_key,
    new_ulid,
)


@pytest.fixture()
def store() -> Store:
    s = Store(":memory:")
    yield s
    s.close()


def _mk_task(store: Store, *, asset: str = "web-01", oracle: str = "sqli_bool_diff") -> Task:
    ts = 1_700_000_000
    t = Task(
        id=new_ulid(ts * 1000),
        engagement_id="ENG-1",
        idem_key=idem_key(engagement_id="ENG-1", asset_id=asset, oracle_id=oracle),
        profile_name="demo-a",
        profile_hash="sha256:" + "a" * 64,
        asset_id=asset,
        oracle_ids=[oracle],
        budget=Budget(),
        created_at=ts,
        updated_at=ts,
    )
    assert store.insert_task(t)
    return t


def test_insert_and_get_task(store: Store) -> None:
    t = _mk_task(store)
    got = store.get_task(t.id)
    assert got is not None
    assert got.id == t.id
    assert got.state is TaskState.QUEUED


def test_duplicate_idem_key_rejected(store: Store) -> None:
    """`ux_tasks_idem` 唯一索引生效：同 key 第二次插入返回 False。"""
    _mk_task(store)
    ts = 1_700_000_001
    dup = Task(
        id=new_ulid(ts * 1000),
        engagement_id="ENG-1",
        idem_key=idem_key(engagement_id="ENG-1", asset_id="web-01", oracle_id="sqli_bool_diff"),
        profile_name="demo-a",
        profile_hash="sha256:" + "a" * 64,
        asset_id="web-01",
        oracle_ids=["sqli_bool_diff"],
        created_at=ts,
        updated_at=ts,
    )
    assert store.insert_task(dup) is False


def test_transition_happy_path(store: Store) -> None:
    t = _mk_task(store)
    assert store.transition(t.id, TaskState.QUEUED, TaskState.LEASED)
    assert store.get_task(t.id).state is TaskState.LEASED


def test_transition_rejects_illegal(store: Store) -> None:
    """DONE 是终态，不许再迁（`ALLOWED_TRANSITIONS[DONE]` 为空）。"""
    t = _mk_task(store)
    store.transition(t.id, TaskState.QUEUED, TaskState.LEASED)
    store.transition(t.id, TaskState.LEASED, TaskState.RUNNING)
    store.transition(t.id, TaskState.RUNNING, TaskState.DONE)
    with pytest.raises(TransitionError):
        store.transition(t.id, TaskState.DONE, TaskState.RUNNING)


def test_transition_lost_returns_false(store: Store) -> None:
    """被抢了返回 False，而不是抛错——调用方据此放弃工作（`03 §2`）。"""
    t = _mk_task(store)
    store.transition(t.id, TaskState.QUEUED, TaskState.LEASED)
    # 已不是 QUEUED，再按 QUEUED->LEASED 迁移必然 0 行
    assert store.transition(t.id, TaskState.QUEUED, TaskState.LEASED) is False


def test_lease_next_is_exclusive(store: Store) -> None:
    """两个 worker 抢同一条任务，只能有一个拿到（`04 §1`）。"""
    _mk_task(store)
    a = store.lease_next("worker-a", now=1_700_000_000)
    b = store.lease_next("worker-b", now=1_700_000_000)
    assert a is not None
    assert b is None, "second worker must not get the same task"


def test_expired_lease_detected(store: Store) -> None:
    """租约过期后可被扫描到（`03 §3`）。"""
    _mk_task(store)
    leased = store.lease_next("worker-a", now=1_700_000_000, ttl_s=30)
    assert leased is not None
    assert store.expired_leases(now=1_700_000_020) == []
    expired = store.expired_leases(now=1_700_000_031)
    assert len(expired) == 1
    assert expired[0].id == leased.id


def test_all_states_covered_by_transition_table() -> None:
    """每个状态都在迁移表里，防止新增状态忘了接线。"""
    assert set(ALLOWED_TRANSITIONS) == set(TaskState)
