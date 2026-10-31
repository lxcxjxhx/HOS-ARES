"""租约：领任务 / 续租 / 释放 / 过期回收（03 §2–§3，04 §2）。

为什么是租约不是分布式锁：锁要释放、会残留；租约自带过期，worker 崩了无需他人善后。

两条铁律：
1. 授时统一走 ``StoreClock``（数据库时间），不用本机时钟——多 worker 时钟漂移是回收误判第一杀手。
2. 回收必须带 ``lease_until < now`` 条件再迁移一次，防止与正在续租的 worker 抢。
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import Protocol

from .models import TaskState
from .store import Store


class Clock(Protocol):
    def now(self) -> int: ...


class StoreClock:
    """统一授时：SQLite 的当前秒。跨 worker 一致，不受本机时钟影响。"""

    def __init__(self, store: Store) -> None:
        self._store = store

    def now(self) -> int:
        # 必须持锁：同一连接被多线程共用时，语句交错会让 fetchone 拿到别人的空结果
        with self._store.lock:
            row = self._store.conn.execute(
                "SELECT CAST(strftime('%s','now') AS INTEGER)"
            ).fetchone()
        if row is None:  # pragma: no cover - 防御：连接已关闭时退化为本机时钟
            return int(time.time())
        return int(row[0])


@dataclass(frozen=True)
class LeaseConfig:
    heartbeat_s: int = 5
    ttl_s: int = 30
    scan_s: int = 5

    @property
    def takeover_bound_s(self) -> int:
        """最坏接管时间：租约 TTL + 一次扫描间隔（默认 35s，B2 DoD 的数字来源）。"""
        return self.ttl_s + self.scan_s


class LeaseLost(RuntimeError):
    """续租失败且已无法挽回：继续干活就会双跑。"""


class Lease:
    def __init__(self, store: Store, clock: Clock, cfg: LeaseConfig | None = None) -> None:
        self.store = store
        self.clock = clock
        self.cfg = cfg or LeaseConfig()

    # ── 领取 ────────────────────────────────────────────────────
    def claim(self, worker_id: str) -> str | None:
        """原子领取一个 QUEUED 任务；同资产已有在跑任务则跳过（04 §4 分区）。"""
        now = self.clock.now()
        with self.store.lock:
            cur = self.store.conn.execute(
                """
                UPDATE tasks
                   SET state = ?, lease_owner = ?, lease_until = ?, heartbeat_at = ?, updated_at = ?
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
                    TaskState.LEASED.value,
                    worker_id,
                    now + self.cfg.ttl_s,
                    now,
                    now,
                    TaskState.QUEUED.value,
                    TaskState.LEASED.value,
                    TaskState.RUNNING.value,
                    TaskState.QUEUED.value,
                ),
            )
            row = cur.fetchone()
            self.store.conn.commit()
        return str(row["id"]) if row is not None else None

    # ── 续租 ────────────────────────────────────────────────────
    def renew(self, task_id: str, worker_id: str) -> bool:
        now = self.clock.now()
        with self.store.lock:
            cur = self.store.conn.execute(
                """
                UPDATE tasks
                   SET lease_until = ?, heartbeat_at = ?, updated_at = ?
                 WHERE id = ? AND lease_owner = ? AND state IN (?, ?)
                """,
                (
                    now + self.cfg.ttl_s,
                    now,
                    now,
                    task_id,
                    worker_id,
                    TaskState.LEASED.value,
                    TaskState.RUNNING.value,
                ),
            )
            self.store.conn.commit()
        return cur.rowcount == 1

    # ── 释放（SIGTERM 走这条，比等租约过期快 30s）────────────────
    def release(self, task_id: str, worker_id: str) -> bool:
        now = self.clock.now()
        with self.store.lock:
            cur = self.store.conn.execute(
                """
                UPDATE tasks
                   SET state = ?, lease_owner = NULL, lease_until = NULL, updated_at = ?
                 WHERE id = ? AND lease_owner = ? AND state IN (?, ?)
                """,
                (
                    TaskState.QUEUED.value,
                    now,
                    task_id,
                    worker_id,
                    TaskState.LEASED.value,
                    TaskState.RUNNING.value,
                ),
            )
            self.store.conn.commit()
        return cur.rowcount == 1

    # ── 回收 ────────────────────────────────────────────────────
    def reap_expired(self) -> list[tuple[str, str]]:
        """回收过期租约。返回 ``[(task_id, 结果状态)]``。

        过期即 ``attempt+1``；``attempt >= max_attempts`` 进死信（03 §3）。
        """
        now = self.clock.now()
        with self.store.lock:
            rows = self.store.conn.execute(
                "SELECT id, attempt, max_attempts FROM tasks"
                " WHERE state IN (?, ?) AND lease_until IS NOT NULL AND lease_until < ?",
                (TaskState.LEASED.value, TaskState.RUNNING.value, now),
            ).fetchall()

            reaped: list[tuple[str, str]] = []
            for row in rows:
                attempt = int(row["attempt"]) + 1
                dead = attempt >= int(row["max_attempts"])
                target = TaskState.DEAD_LETTER if dead else TaskState.QUEUED
                cur = self.store.conn.execute(
                    """
                    UPDATE tasks
                       SET state = ?, attempt = ?, lease_owner = NULL, lease_until = NULL, updated_at = ?
                     WHERE id = ? AND lease_until < ?
                       AND state IN (?, ?)
                    """,
                    (
                        target.value,
                        attempt,
                        now,
                        row["id"],
                        now,
                        TaskState.LEASED.value,
                        TaskState.RUNNING.value,
                    ),
                )
                if cur.rowcount == 1:
                    reaped.append((str(row["id"]), target.value))
            self.store.conn.commit()
        return reaped

    # ── 观测 ────────────────────────────────────────────────────
    def expired_task_ids(self) -> list[str]:
        now = self.clock.now()
        rows = self.store.conn.execute(
            "SELECT id FROM tasks WHERE state IN (?, ?) AND lease_until IS NOT NULL AND lease_until < ?",
            (TaskState.LEASED.value, TaskState.RUNNING.value, now),
        ).fetchall()
        return [str(r["id"]) for r in rows]

    def attempt_of(self, task_id: str) -> int:
        row = self.store.conn.execute("SELECT attempt FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return int(row["attempt"])


def is_transient(exc: BaseException) -> bool:
    """sqlite 忙等属于可重试；其余不在这里猜。"""
    return isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower()
