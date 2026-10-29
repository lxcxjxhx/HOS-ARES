"""调度器（`04 §1`/§2/§6）：回收过期租约、重排、判死信。

**这个模块不执行任务、不发请求**（`01 §4` 模块职责表）。它只做三件事：
  1. 扫描过期租约 → 重排（`attempt+1`）或判死信
  2. 按资产分区保证同资产串行（`04 §4`）
  3. 提供背压快照给 worker 参考

所有时间通过注入的 `Clock` 取，不直接 `time.time()`。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .lease import Clock, LocalClock
from .models import MAX_ATTEMPTS, RECLAIM_SCAN_S, Task, TaskState
from .store import Store

log = logging.getLogger("veripen.scheduler")


class ErrClass(str, Enum):
    """失败分类学（`04 §3`）。分类决定"要不要重试"，不是错误本身决定。"""

    RETRYABLE = "retryable"
    TERMINAL = "terminal"
    NEEDS_HUMAN = "needs_human"


@dataclass(frozen=True)
class ReclaimResult:
    """一次回收扫描的结果，用于日志与演示。"""

    requeued: tuple[str, ...] = ()
    dead_lettered: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        return len(self.requeued) + len(self.dead_lettered) + len(self.skipped)


@dataclass
class Scheduler:
    """租约回收器。

    `max_attempts=3`：`attempt >= 3` 进死信（`03 §3` / `04 §6`）。
    """

    store: Store
    clock: Clock = field(default_factory=LocalClock)
    max_attempts: int = MAX_ATTEMPTS
    scan_interval_s: int = RECLAIM_SCAN_S

    # ---------- 回收 ----------

    def reclaim_once(self) -> ReclaimResult:
        """扫描一次过期租约并处理（`04 §2` 回收扫描 5s 一次）。

        重排规则：`attempt+1`；`attempt >= max_attempts` → `DEAD_LETTER`。
        重排**保留已产出的 capsule**，不重跑已验证 oracle（`03 §3`）。
        """
        now = self.clock.now_s()
        requeued: list[str] = []
        dead: list[str] = []
        skipped: list[str] = []

        for task in self.store.expired_leases(now=now):
            next_attempt = task.attempt + 1
            if next_attempt >= self.max_attempts:
                ok = self.store.transition(
                    task.id,
                    task.state,
                    TaskState.DEAD_LETTER,
                    now=now,
                    attempt=next_attempt,
                    error_class=ErrClass.RETRYABLE.value,
                    error_code="VP-3001",
                )
                (dead if ok else skipped).append(task.id)
            else:
                ok = self.store.transition(
                    task.id,
                    task.state,
                    TaskState.QUEUED,
                    now=now,
                    attempt=next_attempt,
                    lease_owner="",
                    lease_until=0,
                    error_class=ErrClass.RETRYABLE.value,
                    error_code="VP-3001",
                )
                (requeued if ok else skipped).append(task.id)

        # 被抢的（transition 返回 False）记 skipped，不静默吞掉
        for tid in skipped:
            log.warning("reclaim_skipped", extra={"task_id": tid})

        return ReclaimResult(tuple(requeued), tuple(dead), tuple(skipped))

    # ---------- 死信 ----------

    def requeue_dead_letter(self, task_id: str) -> bool:
        """`POST /v1/tasks/{id}/requeue`：人工确认后重排，`attempt` 归零（`24 §2.1`）。"""
        return self.store.transition(
            task_id,
            TaskState.DEAD_LETTER,
            TaskState.QUEUED,
            now=self.clock.now_s(),
            attempt=0,
            error_class="",
            error_code="",
        )

    def dead_letters(self) -> list[Task]:
        rows = self.store._conn.execute(  # noqa: SLF001 — 同包内部
            "SELECT * FROM tasks WHERE state='DEAD_LETTER' ORDER BY updated_at DESC"
        ).fetchall()
        from .store import _row_to_task

        return [_row_to_task(r) for r in rows]

    # ---------- 分区与背压 ----------

    def asset_busy(self, asset_id: str) -> bool:
        """同资产串行（`04 §4`）：该资产有任何 LEASED/RUNNING 任务即忙。"""
        row = self.store._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) n FROM tasks WHERE asset_id=? AND state IN ('LEASED','RUNNING')",
            (asset_id,),
        ).fetchone()
        return bool(row and row["n"] > 0)

    def pick_next_respecting_partition(self, worker_id: str, *, ttl_s: int = 30) -> Task | None:
        """领取任务，但跳过"同资产已有在跑"的（`04 §4` 分区键 = asset_id）。

        实现方式：先列出候选，逐个尝试 `lease_next` 风格的条件更新，
        直到成功或候选耗尽。并发下仍靠 `WHERE state='QUEUED'` 保证不双领。
        """
        now = self.clock.now_s()
        candidates = self.store._conn.execute(  # noqa: SLF001
            "SELECT id, asset_id FROM tasks WHERE state='QUEUED' ORDER BY created_at, id LIMIT 100"
        ).fetchall()
        for cand in candidates:
            if self.asset_busy(cand["asset_id"]):
                continue
            row = self.store._conn.execute(  # noqa: SLF001
                """
                UPDATE tasks SET state='LEASED', lease_owner=?, lease_until=?, heartbeat_at=?, updated_at=?
                 WHERE id=? AND state='QUEUED'
                RETURNING *
                """,
                (worker_id, now + ttl_s, now, now, cand["id"]),
            ).fetchone()
            if row is not None:
                from .store import _row_to_task

                return _row_to_task(row)
        return None

    def backpressure_snapshot(self) -> dict[str, Any]:
        """背压与容量快照（供 `/v1/stats` 与 doctor 用）。"""
        rows = self.store._conn.execute(  # noqa: SLF001
            "SELECT state, COUNT(*) n FROM tasks GROUP BY state"
        ).fetchall()
        by_state = {r["state"]: r["n"] for r in rows}
        return {
            "by_state": by_state,
            "queued": by_state.get(TaskState.QUEUED.value, 0),
            "in_flight": by_state.get(TaskState.LEASED.value, 0) + by_state.get(TaskState.RUNNING.value, 0),
            "dead_letter": by_state.get(TaskState.DEAD_LETTER.value, 0),
            "scan_interval_s": self.scan_interval_s,
            "max_attempts": self.max_attempts,
        }
