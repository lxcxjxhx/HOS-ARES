"""调度与租约测试（`04 §8` 三个必须能现场演示的失败场景，前两个在此机械验证）。"""

from __future__ import annotations

import threading
import time

import pytest

from veripen_core import (
    Budget,
    Store,
    Task,
    TaskState,
    idem_key,
    new_ulid,
)
from veripen_core.lease import Backoff, FrozenClock, HeartbeatThread, LeaseHandle, make_worker_id
from veripen_core.scheduler import Scheduler
from worker.main import Worker


@pytest.fixture()
def store() -> Store:
    s = Store(":memory:")
    yield s
    s.close()


def _seed(store: Store, count: int = 1, *, asset: str = "web-01") -> list[Task]:
    tasks: list[Task] = []
    for i in range(count):
        ts = 1_700_000_000
        t = Task(
            id=new_ulid((ts + i) * 1000),
            engagement_id="ENG-1",
            idem_key=idem_key(engagement_id="ENG-1", asset_id=asset, oracle_id=f"oracle_{i}"),
            profile_name="demo-a",
            profile_hash="sha256:" + "a" * 64,
            asset_id=asset,
            oracle_ids=[f"oracle_{i}"],
            budget=Budget(),
            created_at=ts + i,
            updated_at=ts + i,
        )
        assert store.insert_task(t)
        tasks.append(t)
    return tasks


# ---------- 场景 1：worker 崩溃后任务被接管（`04 §8`）----------


def test_expired_lease_is_reclaimed_within_35s(store: Store) -> None:
    """worker 崩溃 → 租约过期 → ≤35s 内被回收重排，`attempt` 变 1。

    TTL=30s、扫描 5s，所以 35s 是理论上界。这里用 FrozenClock 直接推进时间，
    不真 sleep——测的是逻辑正确性，不是墙钟。
    """
    clock = FrozenClock(1_700_000_000)
    _seed(store)

    worker_a = Worker(store=store, worker_id="worker-a", clock=clock)  # type: ignore[arg-type]
    task = worker_a.run_once()
    assert task is not None

    # 模拟 worker 崩在 RUNNING：手工造一个"领了但没续"的状态
    t = _seed_single_running(store, clock)
    assert t.state is TaskState.RUNNING

    scheduler = Scheduler(store=store, clock=clock)

    # 29 秒：还没到期
    clock.advance(29)
    assert scheduler.reclaim_once().total == 0

    # 31 秒：已过期（在 35s 上界内）
    clock.advance(2)
    result = scheduler.reclaim_once()
    assert t.id in result.requeued, f"task must be requeued, got {result}"

    after = store.get_task(t.id)
    assert after is not None
    assert after.state is TaskState.QUEUED
    assert after.attempt == 1, "attempt must increment on reclaim"


def _seed_single_running(store: Store, clock: FrozenClock) -> Task:
    ts = clock.now_s()
    t = Task(
        id=new_ulid(ts * 1000 + 7),
        engagement_id="ENG-1",
        idem_key=idem_key(engagement_id="ENG-1", asset_id="web-02", oracle_id="crash_test"),
        profile_name="demo-a",
        profile_hash="sha256:" + "b" * 64,
        asset_id="web-02",
        oracle_ids=["crash_test"],
        state=TaskState.LEASED,
        lease_owner="worker-crashed",
        lease_until=ts + 30,
        heartbeat_at=ts,
        budget=Budget(),
        created_at=ts,
        updated_at=ts,
    )
    assert store.insert_task(t)
    assert store.transition(t.id, TaskState.LEASED, TaskState.RUNNING, now=ts)
    got = store.get_task(t.id)
    assert got is not None
    return got


def test_reclaim_goes_to_dead_letter_after_max_attempts(store: Store) -> None:
    """`attempt >= 3` → DEAD_LETTER（`03 §3` / `04 §6`）。"""
    clock = FrozenClock(1_700_000_000)
    ts = clock.now_s()
    t = Task(
        id=new_ulid(ts * 1000),
        engagement_id="ENG-1",
        idem_key=idem_key(engagement_id="ENG-1", asset_id="web-09", oracle_id="x"),
        profile_name="demo-a",
        profile_hash="sha256:" + "c" * 64,
        asset_id="web-09",
        oracle_ids=["x"],
        state=TaskState.LEASED,
        lease_owner="dead",
        lease_until=ts - 1,
        attempt=2,
        budget=Budget(),
        created_at=ts,
        updated_at=ts,
    )
    assert store.insert_task(t)

    scheduler = Scheduler(store=store, clock=clock)
    result = scheduler.reclaim_once()
    assert t.id in result.dead_lettered

    after = store.get_task(t.id)
    assert after is not None
    assert after.state is TaskState.DEAD_LETTER
    assert after.attempt == 3


# ---------- 场景 2：并发 transition 只一个成功（`04 §8` / B2 DoD）----------


def test_concurrent_transition_only_one_wins(store: Store) -> None:
    """两个线程同时对同一 task 做 QUEUED→LEASED，只有 1 个返回 True。

    这是幂等第二道闸（`04 §1` 闸门②）的机械证明。
    """
    _seed(store)
    task = store._conn.execute("SELECT id FROM tasks LIMIT 1").fetchone()["id"]  # noqa: SLF001

    results: list[bool] = []
    barrier = threading.Barrier(2)

    def attempt() -> None:
        barrier.wait()
        results.append(store.transition(task, TaskState.QUEUED, TaskState.LEASED, now=1_700_000_100))

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert sorted(results) == [False, True], f"exactly one must win, got {results}"


def test_concurrent_lease_only_one_worker_gets_task(store: Store) -> None:
    """两个 worker 并发领取，只有一个拿到（`04 §1` 闸门②的调度侧）。"""
    clock = FrozenClock(1_700_000_000)
    _seed(store)

    got: list[str | None] = []
    barrier = threading.Barrier(2)

    def grab(wid: str) -> None:
        s = Scheduler(store=store, clock=clock)
        barrier.wait()
        t = s.pick_next_respecting_partition(wid)
        got.append(t.id if t else None)

    threads = [threading.Thread(target=grab, args=(f"w{i}",)) for i in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    non_none = [g for g in got if g is not None]
    assert len(non_none) == 1, f"exactly one worker must lease, got {got}"


# ---------- 租约续租与丢失检测 ----------


def test_lease_renew_succeeds_for_owner(store: Store) -> None:
    clock = FrozenClock(1_700_000_000)
    _seed(store)
    task = Scheduler(store=store, clock=clock).pick_next_respecting_partition("w-a")
    assert task is not None

    lease = LeaseHandle(task_id=task.id, worker_id="w-a", store=store, clock=clock)
    assert lease.renew() is True
    assert lease.lost is False


def test_lease_lost_after_two_failures(store: Store) -> None:
    """连续 2 次续租失败 = 丢失（`04 §2`）。"""
    clock = FrozenClock(1_700_000_000)
    _seed(store)
    task = Scheduler(store=store, clock=clock).pick_next_respecting_partition("w-a")
    assert task is not None

    # 假装租约已被别人接走
    store._conn.execute("UPDATE tasks SET lease_owner='w-b' WHERE id=?", (task.id,))  # noqa: SLF001

    lease = LeaseHandle(task_id=task.id, worker_id="w-a", store=store, clock=clock)
    assert lease.renew() is False
    assert lease.lost is False, "one failure is not yet lost"
    assert lease.renew() is False
    assert lease.lost is True, "two consecutive failures means lost"


def test_release_returns_to_queued_without_attempt_increment(store: Store) -> None:
    """SIGTERM 主动释放：回 QUEUED 且 `attempt` 不变（`04 §2`）。"""
    clock = FrozenClock(1_700_000_000)
    _seed(store)
    task = Scheduler(store=store, clock=clock).pick_next_respecting_partition("w-a")
    assert task is not None
    before = store.get_task(task.id)
    assert before is not None

    lease = LeaseHandle(task_id=task.id, worker_id="w-a", store=store, clock=clock)
    assert lease.release() is True

    after = store.get_task(task.id)
    assert after is not None
    assert after.state is TaskState.QUEUED
    assert after.attempt == before.attempt


# ---------- 分区串行（`04 §4`）----------


def test_same_asset_is_serialized(store: Store) -> None:
    """同资产已有在跑任务时，不再领同资产的下一个（`04 §4`）。"""
    clock = FrozenClock(1_700_000_000)
    _seed(store, count=2, asset="web-01")

    sched = Scheduler(store=store, clock=clock)
    first = sched.pick_next_respecting_partition("w-a")
    assert first is not None
    assert sched.asset_busy("web-01") is True
    assert sched.pick_next_respecting_partition("w-b") is None, "same asset must be serialized"


def test_different_assets_run_in_parallel(store: Store) -> None:
    clock = FrozenClock(1_700_000_000)
    _seed(store, count=1, asset="web-01")
    _seed(store, count=1, asset="web-02")

    sched = Scheduler(store=store, clock=clock)
    a = sched.pick_next_respecting_partition("w-a")
    b = sched.pick_next_respecting_partition("w-b")
    assert a is not None and b is not None
    assert a.asset_id != b.asset_id


# ---------- 退避（`04 §3`）----------


@pytest.mark.parametrize(
    ("attempt", "expected_base"),
    [(0, 1.0), (1, 4.0), (2, 16.0), (3, 16.0), (99, 16.0)],
)
def test_backoff_schedule(attempt: int, expected_base: float) -> None:
    """1s,4s,16s 且超出后钳在 16s。rng 固定 0.5 → 无抖动。"""
    b = Backoff(attempt=attempt, rng=lambda: 0.5)
    assert b.seconds() == pytest.approx(expected_base)


def test_backoff_jitter_within_20_percent() -> None:
    lo = Backoff(attempt=0, rng=lambda: 0.0).seconds()
    hi = Backoff(attempt=0, rng=lambda: 0.999).seconds()
    assert 1.0 * 0.8 <= lo <= 1.0
    assert 1.0 <= hi <= 1.0 * 1.2


def test_backoff_exhausted_at_max_attempts() -> None:
    assert Backoff(attempt=2).exhausted is False
    assert Backoff(attempt=3).exhausted is True


# ---------- worker 端到端 ----------


def test_worker_run_once_completes_task(store: Store) -> None:
    clock = FrozenClock(1_700_000_000)
    _seed(store)
    w = Worker(store=store, worker_id="w-test", clock=clock)  # type: ignore[arg-type]
    task = w.run_once()
    assert task is not None
    final = store.get_task(task.id)
    assert final is not None
    assert final.state is TaskState.DONE
    assert final.result is not None


def test_worker_id_is_unique() -> None:
    a = make_worker_id()
    b = make_worker_id()
    assert a != b
    assert a.startswith(make_worker_id().split("-")[0])
