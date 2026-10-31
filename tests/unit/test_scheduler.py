"""B2 调度测试：租约 / 心跳 / 回收 / 并发迁移（04 §2–§3，03 §2–§3）。

全部用短 TTL（1–2s）跑，逻辑与默认 35s 接管同一套代码。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from veripen_core.lease import Lease, LeaseConfig, LeaseLost, StoreClock
from veripen_core.models import TaskState
from veripen_core.scheduler import Scheduler
from veripen_core.store import Store
from worker.heartbeat import Heartbeat
from worker.worker import Worker


class ManualClock:
    """可推进的时钟（真实接缝：Lease 只依赖 Clock 协议）。"""

    def __init__(self, start: int = 1_000) -> None:
        self.value = start

    def now(self) -> int:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += seconds


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "sched.db").init()
    yield s
    s.close()


def _submit(
    store: Store,
    asset: str = "web-01",
    oracle: str = "idor_cross_account",
    max_attempts: int = 3,
) -> str:
    tid, _ = store.submit_task(
        engagement_id="ENG-1",
        profile_name="demo-a",
        profile_hash="h1",
        asset_id=asset,
        oracle_ids=[oracle],
        max_attempts=max_attempts,
    )
    return tid


def test_claim_then_second_claim_gets_nothing(store: Store) -> None:
    lease = Lease(store, ManualClock())
    tid = _submit(store)
    assert lease.claim("w1") == tid
    assert lease.claim("w2") is None


def test_same_asset_is_serialized(store: Store) -> None:
    lease = Lease(store, ManualClock())
    first = _submit(store, asset="web-01")
    _submit(store, asset="web-01", oracle="sqli_bool_diff")
    other = _submit(store, asset="web-02")

    assert lease.claim("w1") == first
    # 同资产并行被禁止；不同资产可以并行
    assert lease.claim("w2") == other


def test_renew_extends_and_wrong_owner_fails(store: Store) -> None:
    clock = ManualClock()
    lease = Lease(store, clock)
    tid = _submit(store)
    lease.claim("w1")
    row = store.get_task(tid)
    assert row is not None and row["lease_until"] == clock.now() + 30

    clock.advance(10)
    assert lease.renew(tid, "w1") is True
    assert store.get_task(tid)["lease_until"] == clock.now() + 30  # type: ignore[index]
    assert lease.renew(tid, "w2") is False


def test_reap_requeues_with_attempt_plus_one(store: Store) -> None:
    clock = ManualClock()
    lease = Lease(store, clock)
    tid = _submit(store)
    lease.claim("w1")

    clock.advance(31)
    reaped = lease.reap_expired()
    assert reaped == [(tid, TaskState.QUEUED.value)]
    row = store.get_task(tid)
    assert row is not None
    assert row["state"] == TaskState.QUEUED.value
    assert row["attempt"] == 1
    assert row["lease_owner"] is None


def test_reaped_task_can_be_taken_by_another_worker(store: Store) -> None:
    clock = ManualClock()
    lease = Lease(store, clock)
    tid = _submit(store)
    assert lease.claim("w1") == tid
    clock.advance(31)
    lease.reap_expired()
    assert lease.claim("w2") == tid
    assert store.get_task(tid)["attempt"] == 1  # type: ignore[index]


def test_attempts_exhaust_into_dead_letter(store: Store) -> None:
    clock = ManualClock()
    lease = Lease(store, clock)
    tid = _submit(store, max_attempts=2)
    for _ in range(2):
        assert lease.claim("w1") == tid
        clock.advance(31)
        lease.reap_expired()
    row = store.get_task(tid)
    assert row is not None
    assert row["state"] == TaskState.DEAD_LETTER.value
    assert row["attempt"] == 2


def test_release_puts_task_back_without_attempt_increment(store: Store) -> None:
    lease = Lease(store, ManualClock())
    tid = _submit(store)
    lease.claim("w1")
    assert lease.release(tid, "w1") is True
    row = store.get_task(tid)
    assert row is not None
    assert row["state"] == TaskState.QUEUED.value
    assert row["attempt"] == 0
    assert lease.release(tid, "w1") is False  # 已不是它的租约


def test_concurrent_transition_only_one_wins(store: Store) -> None:
    tid = _submit(store)
    results: list[bool] = []
    barrier = threading.Barrier(2)
    lock = threading.Lock()

    def race() -> None:
        barrier.wait()
        ok = store.transition(tid, TaskState.QUEUED, TaskState.LEASED)
        with lock:
            results.append(ok)

    threads = [threading.Thread(target=race) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == [False, True]


def test_concurrent_claim_single_winner(store: Store) -> None:
    lease = Lease(store, StoreClock(store))
    _submit(store)
    claimed: list[str | None] = []
    barrier = threading.Barrier(6)
    lock = threading.Lock()

    def race(i: int) -> None:
        barrier.wait()
        got = lease.claim(f"w{i}")
        with lock:
            claimed.append(got)

    threads = [threading.Thread(target=race, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len([c for c in claimed if c]) == 1


def test_scheduler_tick_runs_handler_and_marks_running(store: Store) -> None:
    lease = Lease(store, ManualClock())
    seen: list[str] = []
    scheduler = Scheduler(store, lease, lambda tid: seen.append(tid))
    _submit(store)

    report = scheduler.tick("w1")
    assert report.claimed is not None
    assert report.handled is True
    assert seen == [report.claimed]
    assert store.get_task(report.claimed)["state"] == TaskState.RUNNING.value  # type: ignore[index]


def test_heartbeat_renews_lease(store: Store) -> None:
    cfg = LeaseConfig(heartbeat_s=1, ttl_s=2)
    lease = Lease(store, StoreClock(store), cfg)
    tid = _submit(store)
    lease.claim("w1")
    before = store.get_task(tid)["lease_until"]  # type: ignore[index]

    hb = Heartbeat(lease, tid, "w1")
    hb.start()
    try:
        deadline = time.monotonic() + 5
        while hb.beats < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        hb.stop()
        hb.join(timeout=2)

    assert hb.beats >= 2
    assert store.get_task(tid)["lease_until"] >= before  # type: ignore[index]
    assert lease.expired_task_ids() == []


def test_heartbeat_detects_lost_lease(store: Store) -> None:
    cfg = LeaseConfig(heartbeat_s=1, ttl_s=2)
    lease = Lease(store, StoreClock(store), cfg)
    tid = _submit(store)
    lease.claim("w1")
    lease.release(tid, "w1")  # 租约被收回

    lost: list[str] = []
    hb = Heartbeat(lease, tid, "w1", on_lost=lambda: lost.append(tid))
    hb.start()
    try:
        deadline = time.monotonic() + 6
        while not lost and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        hb.stop()
        hb.join(timeout=2)

    assert lost == [tid]


def test_worker_shutdown_releases_lease(store: Store) -> None:
    worker = Worker(store, "w1", cfg=LeaseConfig(heartbeat_s=1, ttl_s=2))
    tid = _submit(store)
    worker.current = tid
    lease = worker.lease
    lease.claim("w1")
    worker.current = tid
    worker.shutdown()
    assert store.get_task(tid)["state"] == TaskState.QUEUED.value  # type: ignore[index]


def test_worker_runs_pending_tasks(store: Store) -> None:
    worker = Worker(store, "w1", cfg=LeaseConfig(heartbeat_s=1, ttl_s=2), hold_s=0.0)
    _submit(store, asset="web-01")
    _submit(store, asset="web-02")
    assert worker.run_until_idle() == 2


def test_lease_lost_type_is_raisable() -> None:
    with pytest.raises(LeaseLost):
        raise LeaseLost("boom")
