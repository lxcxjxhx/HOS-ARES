"""B2 关键 DoD：真实 `kill -9` worker 后任务在 35s 内被另一 worker 接管。

这个测试**真的起子进程再真的杀掉**，不是 mock。理由（`21 §2` 的验收口径）：
"kill -9 worker 后 35s 内任务被接管"——只有真杀进程才能证明租约机制真的生效，
用 `FrozenClock` 推进时间只能证明回收逻辑，不能证明"进程崩了别人能接"。

设计：
  - 子进程 worker 领到任务后停在 RUNNING 并长睡（模拟执行中被杀）
  - 父进程 `SIGKILL` 它（Windows 用 TerminateProcess，等价 kill -9）
  - 另一个 worker 在 TTL(30s) 过期后应能领到同一任务

耗时约 32s（要等真实 TTL 过期），标 `slow`。
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from veripen_core import Budget, Store, Task, TaskState, idem_key, new_ulid
from veripen_core.lease import LocalClock
from veripen_core.scheduler import Scheduler

REPO_ROOT = Path(__file__).resolve().parents[2]

WORKER_SCRIPT = textwrap.dedent(
    """
    import os, sys, time
    sys.path.insert(0, r"{root}")
    os.makedirs(r"{var}", exist_ok=True)
    from veripen_core.store import Store
    from veripen_core.models import TaskState
    from veripen_core.scheduler import Scheduler

    store = Store(r"{db}")
    sched = Scheduler(store=store)
    task = sched.pick_next_respecting_partition("victim-worker")
    if task is None:
        print("NO_TASK", flush=True)
        sys.exit(4)
    store.transition(task.id, TaskState.LEASED, TaskState.RUNNING)
    print("HOLDING " + task.id, flush=True)
    # 长睡：模拟执行中被 kill -9，不发 SIGTERM
    time.sleep(300)
    """
)


@pytest.fixture()
def file_store(tmp_path: Path) -> Store:
    db = tmp_path / "var" / "veripen.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    s = Store(str(db))
    yield s
    s.close()


def _seed(store: Store) -> Task:
    ts = int(time.time())
    t = Task(
        id=new_ulid(),
        engagement_id="ENG-KILL",
        idem_key=idem_key(engagement_id="ENG-KILL", asset_id="web-kill", oracle_id="sqli_bool_diff"),
        profile_name="demo-a",
        profile_hash="sha256:" + "d" * 64,
        asset_id="web-kill",
        oracle_ids=["sqli_bool_diff"],
        budget=Budget(),
        created_at=ts,
        updated_at=ts,
    )
    assert store.insert_task(t)
    return t


@pytest.mark.slow
def test_kill9_worker_task_taken_over(file_store: Store, tmp_path: Path) -> None:
    """真实杀进程 → 租约过期 → 另一 worker 接管，全程 ≈32s。"""
    task = _seed(file_store)
    db_path = str(file_store.path)

    script = WORKER_SCRIPT.format(root=str(REPO_ROOT), var=str(tmp_path / "var"), db=db_path)
    script_path = tmp_path / "victim_worker.py"
    script_path.write_text(script, encoding="utf-8")

    proc = subprocess.Popen(
        [sys.executable, str(script_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        # 等它拿到任务
        line = proc.stdout.readline() if proc.stdout else ""
        assert line.startswith("HOLDING "), f"victim worker failed to lease: {line!r}"
        held_id = line.strip().split()[1]
        assert held_id == task.id

        mid = file_store.get_task(task.id)
        assert mid is not None
        assert mid.state is TaskState.RUNNING
        assert mid.lease_owner == "victim-worker"

        # kill -9（Windows 上 TerminateProcess 等价）
        kill_start = time.monotonic()
        proc.kill()
        proc.wait(timeout=10)

        # 立刻尝试接管：租约还没过期，应该拿不到
        sched = Scheduler(store=file_store)
        assert sched.reclaim_once().total == 0, "lease must not be reclaimed before TTL"
        early = sched.pick_next_respecting_partition("rescuer-worker")
        assert early is None, "must not steal an unexpired lease"

        # 等真实 TTL 过期（30s）+ 扫描余量 → 上界 35s
        takeover = None
        while time.monotonic() - kill_start < 35:
            sched.reclaim_once()
            takeover = sched.pick_next_respecting_partition("rescuer-worker")
            if takeover is not None:
                break
            time.sleep(1)

        elapsed = time.monotonic() - kill_start
        assert takeover is not None, f"task not taken over within 35s (waited {elapsed:.1f}s)"
        assert takeover.id == task.id
        assert elapsed <= 35, f"takeover took {elapsed:.1f}s, exceeds 35s bound"

        final = file_store.get_task(task.id)
        assert final is not None
        assert final.lease_owner == "rescuer-worker"
        assert final.attempt == 1, "attempt must increment to 1 after takeover"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
