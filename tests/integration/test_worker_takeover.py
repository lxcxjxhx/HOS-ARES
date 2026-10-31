"""B2 进程级验收：worker 被 kill -9 后，任务在接管窗口内被另一个 worker 接走。

这是 04 §8 的第二场演示（重复提交 / worker 崩溃 / 打得太快 / 端点消失）里的「worker 崩溃」。
用短 TTL 跑同一套代码；默认配置的接管上界 = ttl 30s + scan 5s = 35s（03 §3）。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from veripen_core.lease import Lease, LeaseConfig, StoreClock
from veripen_core.models import TaskState
from veripen_core.store import Store

ROOT = Path(__file__).resolve().parents[2]

TTL_S = 2
SCAN_S = 1
HEARTBEAT_S = 1
TAKEOVER_BOUND_S = TTL_S + SCAN_S + 3  # 留出进程启动与两次轮询的余量


def _spawn_worker(db: Path, hold_s: int) -> subprocess.Popen[bytes]:
    env = dict(os.environ)
    env.update(
        {
            "VERIPEN_DB": str(db),
            "VERIPEN_LEASE_TTL_S": str(TTL_S),
            "VERIPEN_SCAN_S": str(SCAN_S),
            "VERIPEN_HEARTBEAT_S": str(HEARTBEAT_S),
            "VERIPEN_WORKER_HOLD_S": str(hold_s),
            "PYTHONPATH": str(ROOT),
            "PYTHONIOENCODING": "utf-8",
        }
    )
    return subprocess.Popen(
        [sys.executable, "-m", "worker.worker"],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_state(store: Store, task_id: str, states: set[str], timeout: float) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = store.get_task(task_id)
        if row is not None and row["state"] in states:
            return str(row["state"])
        time.sleep(0.05)
    row = store.get_task(task_id)
    raise AssertionError(f"等待状态 {states} 超时，当前 {row['state'] if row else None}")


def test_worker_killed_leaves_task_takeover(tmp_path: Path) -> None:
    db = tmp_path / "takeover.db"
    store = Store(db).init()
    task_id, _ = store.submit_task(
        engagement_id="ENG-1",
        profile_name="demo-a",
        profile_hash="h1",
        asset_id="web-01",
        oracle_ids=["idor_cross_account"],
    )

    worker = _spawn_worker(db, hold_s=60)
    try:
        _wait_state(store, task_id, {TaskState.LEASED.value, TaskState.RUNNING.value}, timeout=15)
        owner = store.get_task(task_id)["lease_owner"]  # type: ignore[index]
        assert owner, "被领取后必须有 lease_owner"
        worker.kill()  # 相当于 docker kill / kill -9
        worker.wait(timeout=10)
    finally:
        if worker.poll() is None:  # pragma: no cover
            worker.kill()

    lease = Lease(store, StoreClock(store), LeaseConfig(ttl_s=TTL_S, scan_s=SCAN_S))
    started = time.monotonic()
    taken: str | None = None
    while time.monotonic() - started < TAKEOVER_BOUND_S:
        lease.reap_expired()
        taken = lease.claim("worker-2")
        if taken == task_id:
            break
        time.sleep(0.05)

    elapsed = time.monotonic() - started
    assert taken == task_id, "任务未被接管"
    assert elapsed <= TAKEOVER_BOUND_S, elapsed

    row = store.get_task(task_id)
    assert row is not None
    assert row["attempt"] == 1
    assert row["lease_owner"] == "worker-2"
    store.close()


def test_default_takeover_bound_is_35s() -> None:
    cfg = LeaseConfig()
    assert (cfg.ttl_s, cfg.scan_s, cfg.heartbeat_s) == (30, 5, 5)
    assert cfg.takeover_bound_s == 35
