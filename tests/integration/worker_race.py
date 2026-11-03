"""B13 · 3 worker 并行不重不漏（04 §4、21 §B13）。

"不重不漏"不是靠 worker 之间通信，是靠 **`transition(... WHERE state=from)` 的条件更新**：
赢的那个 `rowcount == 1`，输的那个拿到 0 行就必须放弃。这是 03 §2 的原话——
「没有这一步，重复执行必然发生」。

测试用一个真 SQLite 文件 + 三个线程去抢同一批任务，断言：

1. **每个任务恰好被领走一次**（不重）；
2. **没有任务被留下**（不漏）；
3. **同一资产不会同时跑两个**（分区约束）；
4. **死掉 worker 的租约会被回收，任务重新可领**（不因崩溃而永久卡住）。

做不到的（本机无 redis / 无容器）：真租约授时、真进程崩溃。
所以这里用 ``Store`` 的文件库模拟并发，并且在 ``note`` 里写明"这是并发模型验证，不是容器验证"。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from veripen_core.models import TaskState
from veripen_core.store import Store


@dataclass
class ClaimLog:
    """谁领到了什么。**这是"不重不漏"的唯一证据来源。**"""

    claims: list[tuple[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, worker: str, task_id: str) -> None:
        with self._lock:
            self.claims.append((worker, task_id))

    def fail(self, msg: str) -> None:
        with self._lock:
            self.errors.append(msg)

    def by_task(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for worker, tid in self.claims:
            out.setdefault(tid, []).append(worker)
        return out


def _claim_one(store: Store, worker: str, *, now: int, ttl_s: int = 60) -> str | None:
    """原子领取：**就是 B2 那条 SQL 的语义**，这里单列出来便于验证。"""
    with store.lock:
        row = store.conn.execute(
            """
            UPDATE tasks
               SET state = ?, lease_owner = ?, lease_until = ?, updated_at = ?
             WHERE id = (
                    SELECT id FROM tasks t
                     WHERE t.state = ?
                       AND NOT EXISTS (
                             SELECT 1 FROM tasks t2
                              WHERE t2.asset_id = t.asset_id
                                AND t2.state IN (?, ?))
                     ORDER BY t.created_at, t.id
                     LIMIT 1)
               AND state = ?
            RETURNING id
            """,
            (
                TaskState.LEASED.value, worker, now + ttl_s, now,
                TaskState.QUEUED.value,
                TaskState.LEASED.value, TaskState.RUNNING.value,
                TaskState.QUEUED.value,
            ),
        ).fetchone()
        store.conn.commit()
    return str(row["id"]) if row is not None else None


def run_workers(
    db_path: str | Path, workers: int, *, tasks: int, now: int = 1_700_000_000, ttl_s: int = 60
) -> ClaimLog:
    """起 N 个线程抢 ``tasks`` 个任务，每个任务抢到就标 RUNNING 并释放。

    **每个 worker 独立连接**：共用一个连接测不出并发语义（SQLite 会串行化）。
    """
    store = Store(db_path)
    store.init()
    store.conn.execute("DELETE FROM tasks")
    store.conn.commit()

    for i in range(tasks):
        store.conn.execute(
            "INSERT INTO tasks (id, engagement_id, idem_key, profile_name, profile_hash,"
            " asset_id, oracle_ids, state, attempt, max_attempts, budget_json,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"t{i:03d}", "e", f"k{i}", "p", "h", f"a{i:03d}", '["o"]',
             TaskState.QUEUED.value, 0, 3, "{}", now + i, now + i),
        )
    store.conn.commit()
    store.close()

    log = ClaimLog()
    barrier = threading.Barrier(workers)

    def worker_main(idx: int) -> None:
        conn = Store(db_path)
        conn.init()
        name = f"w{idx}"
        try:
            barrier.wait(timeout=10)  # 尽量让它们同时起跑，放大竞争
        except threading.BrokenBarrierError:
            pass
        while True:
            try:
                tid = _claim_one(conn, name, now=now, ttl_s=ttl_s)
            except Exception as exc:  # noqa: BLE001 - 领取失败要记账，不能静默
                log.fail(f"{name}: {type(exc).__name__}: {exc}")
                break
            if tid is None:
                break
            log.add(name, tid)
            conn.conn.execute(
                "UPDATE tasks SET state=? WHERE id=?", (TaskState.RUNNING.value, tid)
            )
            conn.conn.commit()
        conn.close()

    threads = [threading.Thread(target=worker_main, args=(i,)) for i in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return log


def task_states(db_path: str | Path) -> dict[str, str]:
    store = Store(db_path)
    store.init()
    try:
        rows = store.conn.execute("SELECT id, state FROM tasks").fetchall()
    finally:
        store.close()
    return {str(r["id"]): str(r["state"]) for r in rows}


def reap_and_reclaim(db_path: str | Path, *, worker: str, now: int) -> str | None:
    """回收过期租约后重新领取。**崩掉的 worker 不该让任务永久卡住。**"""
    store = Store(db_path)
    store.init()
    with store.lock:
        store.conn.execute(
            "UPDATE tasks SET state=?, lease_owner=NULL, lease_until=NULL"
            " WHERE state IN (?, ?) AND IFNULL(lease_until, 0) < ?",
            (TaskState.QUEUED.value, TaskState.LEASED.value, TaskState.RUNNING.value, now),
        )
        store.conn.commit()
    tid = _claim_one(store, worker, now=now)
    store.close()
    return tid


def summary(log: ClaimLog, states: dict[str, str]) -> dict[str, Any]:
    by_task = log.by_task()
    dup = {t: w for t, w in by_task.items() if len(w) > 1}
    unclaimed = [t for t, s in states.items() if s == TaskState.QUEUED.value]
    return {
        "tasks": len(states),
        "claims": len(log.claims),
        "duplicates": sorted(dup),
        "unclaimed": sorted(unclaimed),
        "errors": list(log.errors),
        "clean": not dup and not unclaimed and not log.errors,
    }


__all__ = [
    "ClaimLog",
    "reap_and_reclaim",
    "run_workers",
    "summary",
    "task_states",
]
