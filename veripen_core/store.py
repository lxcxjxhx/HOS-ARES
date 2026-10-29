"""SQLite 存储层（`03 §1` 六张表的可执行版本）。

设计要点：
  - 迁移只前进，`001_init.sql` 是唯一 DDL 来源，测试里也用它建库（避免两处漂移）。
  - `transition()` 是唯一的任务状态迁移入口，`WHERE state=from` 条件更新（`03 §2`）。
  - 时钟可注入：测试与 replay 需要确定性，所以 `now()` 走参数而不是 `time.time()`。
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .models import (
    ALLOWED_TRANSITIONS,
    Approval,
    ApprovalState,
    Budget,
    CapsuleMeta,
    Finding,
    Task,
    TaskEvent,
    TaskState,
)
from .oracle_api import FindingState, canonical_json

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
INIT_SQL = MIGRATIONS_DIR / "001_init.sql"


def utc_now() -> int:
    """统一时钟：UTC 秒。测试可 monkeypatch。"""
    return int(time.time())


class TransitionError(RuntimeError):
    """非法状态迁移（不在 `ALLOWED_TRANSITIONS` 里）。"""


class Store:
    """单进程 SQLite 存储。交付档切 PG 时只换实现，接口不变（`17 §3` O3）。"""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self.migrate()

    # ---------- 生命周期 ----------

    def migrate(self) -> None:
        sql = INIT_SQL.read_text(encoding="utf-8")
        self._conn.executescript(sql)

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """显式事务。SQLite 的 `isolation_level=None` 下需手写 BEGIN。"""
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT")

    # ---------- tasks ----------

    def insert_task(self, task: Task) -> bool:
        """插入任务。`idem_key` 唯一冲突时返回 False（调用方改走"复用已有"路径）。"""
        try:
            with self.tx() as c:
                c.execute(
                    """
                    INSERT INTO tasks (id, engagement_id, idem_key, profile_name, profile_hash,
                                       asset_id, oracle_ids, state, lease_owner, lease_until,
                                       heartbeat_at, attempt, max_attempts, error_class, error_code,
                                       budget_json, result_json, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        task.id,
                        task.engagement_id,
                        task.idem_key,
                        task.profile_name,
                        task.profile_hash,
                        task.asset_id,
                        canonical_json(task.oracle_ids),
                        task.state.value,
                        task.lease_owner,
                        task.lease_until,
                        task.heartbeat_at,
                        task.attempt,
                        task.max_attempts,
                        task.error_class,
                        task.error_code,
                        canonical_json(task.budget.model_dump()),
                        canonical_json(task.result) if task.result is not None else None,
                        task.created_at,
                        task.updated_at,
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def get_task(self, task_id: str) -> Task | None:
        row = self._conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return _row_to_task(row) if row else None

    def find_task_by_idem(self, key: str) -> Task | None:
        row = self._conn.execute("SELECT * FROM tasks WHERE idem_key=?", (key,)).fetchone()
        return _row_to_task(row) if row else None

    def transition(
        self,
        task_id: str,
        frm: TaskState,
        to: TaskState,
        *,
        now: int | None = None,
        budget: Budget | None = None,
        result: dict[str, Any] | None = None,
        attempt: int | None = None,
        lease_owner: str | None = None,
        lease_until: int | None = None,
        heartbeat_at: int | None = None,
        error_class: str | None = None,
        error_code: str | None = None,
    ) -> bool:
        """唯一状态迁移入口（`03 §2`）。

        返回 False 表示"被抢了"——调用方必须放弃当前工作，不许继续。
        先查迁移表再改库：非法迁移是编程错误，抛 `TransitionError` 而非静默返回 False。
        """
        if to not in ALLOWED_TRANSITIONS[frm]:
            raise TransitionError(f"illegal transition {frm.value} -> {to.value} (03 §2)")

        ts = utc_now() if now is None else now
        sets = ["state=?", "updated_at=?"]
        vals: list[Any] = [to.value, ts]
        if budget is not None:
            sets.append("budget_json=?")
            vals.append(canonical_json(budget.model_dump()))
        if result is not None:
            sets.append("result_json=?")
            vals.append(canonical_json(result))
        if attempt is not None:
            sets.append("attempt=?")
            vals.append(attempt)
        if lease_owner is not None:
            sets.append("lease_owner=?")
            vals.append(lease_owner)
        if lease_until is not None:
            sets.append("lease_until=?")
            vals.append(lease_until)
        if heartbeat_at is not None:
            sets.append("heartbeat_at=?")
            vals.append(heartbeat_at)
        if error_class is not None:
            sets.append("error_class=?")
            vals.append(error_class)
        if error_code is not None:
            sets.append("error_code=?")
            vals.append(error_code)

        cur = self._conn.execute(
            f"UPDATE tasks SET {', '.join(sets)} WHERE id=? AND state=?",
            (*vals, task_id, frm.value),
        )
        return cur.rowcount == 1

    def lease_next(self, worker_id: str, *, now: int | None = None, ttl_s: int = 30) -> Task | None:
        """原子领取一个 QUEUED 任务（`04 §1`）。

        单条 UPDATE ... RETURNING 保证两个 worker 不会领到同一条。
        """
        ts = utc_now() if now is None else now
        row = self._conn.execute(
            """
            UPDATE tasks
               SET state='LEASED', lease_owner=?, lease_until=?, heartbeat_at=?, updated_at=?
             WHERE id = (
                   SELECT id FROM tasks WHERE state='QUEUED' ORDER BY created_at, id LIMIT 1
             ) AND state='QUEUED'
            RETURNING *
            """,
            (worker_id, ts + ttl_s, ts, ts),
        ).fetchone()
        return _row_to_task(row) if row else None

    def expired_leases(self, *, now: int | None = None) -> list[Task]:
        ts = utc_now() if now is None else now
        rows = self._conn.execute(
            "SELECT * FROM tasks WHERE state IN ('LEASED','RUNNING') AND lease_until IS NOT NULL AND lease_until < ?",
            (ts,),
        ).fetchall()
        return [_row_to_task(r) for r in rows]

    # ---------- task_events ----------

    def append_event(self, ev: TaskEvent) -> int:
        """写事件。`detail_json` 超 2KB 直接拒绝（`03 §6`）。"""
        payload = canonical_json(ev.detail)
        if len(payload.encode("utf-8")) > 2048:
            raise ValueError("task_event.detail exceeds 2KB (03 §6); full trace belongs in out/")
        cur = self._conn.execute(
            """INSERT INTO task_events (task_id, ts, kind, oracle_id, tool, elapsed_ms, tokens, ok, err_class, detail_json)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                ev.task_id,
                ev.ts,
                ev.kind,
                ev.oracle_id,
                ev.tool,
                ev.elapsed_ms,
                ev.tokens,
                1 if ev.ok else 0,
                ev.err_class,
                payload,
            ),
        )
        return int(cur.lastrowid or 0)

    def events(self, task_id: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM task_events WHERE task_id=? ORDER BY seq", (task_id,)
        ).fetchall()

    # ---------- findings / capsules ----------

    def upsert_finding(self, f: Finding) -> None:
        self._conn.execute(
            """INSERT INTO findings
                 (id, task_id, asset_id, oracle_id, oracle_version, title, severity,
                  confidence, cwe, state, capsule_id, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(task_id, oracle_id, asset_id) DO UPDATE SET
                 title=excluded.title, severity=excluded.severity, confidence=excluded.confidence,
                 cwe=excluded.cwe, state=excluded.state, capsule_id=excluded.capsule_id""",
            (
                f.id,
                f.task_id,
                f.asset_id,
                f.oracle_id,
                f.oracle_version,
                f.title,
                f.severity.value,
                f.confidence,
                f.cwe,
                f.state.value,
                f.capsule_id,
                f.created_at,
            ),
        )

    def get_finding(self, finding_id: str) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM findings WHERE id=?", (finding_id,)).fetchone()

    def findings_for_task(self, task_id: str) -> list[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM findings WHERE task_id=? ORDER BY id", (task_id,)).fetchall()

    def insert_capsule(self, meta: CapsuleMeta) -> None:
        self._conn.execute(
            """INSERT INTO capsules (id, finding_id, path, self_sha256, prev_sha256, sealed_at, replay_status, replay_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                meta.id,
                meta.finding_id,
                meta.path,
                meta.self_sha256,
                meta.prev_sha256,
                meta.sealed_at,
                meta.replay_status,
                meta.replay_at,
            ),
        )

    def last_capsule_sha(self) -> str | None:
        row = self._conn.execute(
            "SELECT self_sha256 FROM capsules ORDER BY sealed_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
        return row["self_sha256"] if row else None

    # ---------- approvals ----------

    def insert_approval(self, a: Approval) -> None:
        self._conn.execute(
            """INSERT INTO approvals (id, task_id, action_class, impact_json, state, requested_at,
                                      decided_at, decided_by, reason)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                a.id,
                a.task_id,
                a.action_class,
                canonical_json(a.impact),
                a.state.value,
                a.requested_at,
                a.decided_at,
                a.decided_by,
                a.reason,
            ),
        )

    def decide_approval(self, approval_id: str, decision: ApprovalState, by: str, reason: str, *, now: int | None = None) -> bool:
        """审批决定。`by` 由上层强校验不得等于 lease_owner（`24 §2.3`）。"""
        ts = utc_now() if now is None else now
        cur = self._conn.execute(
            "UPDATE approvals SET state=?, decided_at=?, decided_by=?, reason=? WHERE id=? AND state='PENDING'",
            (decision.value, ts, by, reason, approval_id),
        )
        return cur.rowcount == 1

    # ---------- 一致性自检（`03 §7`）----------

    def self_check(self) -> dict[str, Any]:
        """`make doctor --deep` 的五条查询。第 5 条非 0 = 事故。"""
        q = {
            "duplicate_idem_keys": "SELECT idem_key, COUNT(*) c FROM tasks GROUP BY idem_key HAVING c>1",
            "stuck_tasks": (
                "SELECT id FROM tasks WHERE state IN('LEASED','RUNNING') "
                "AND lease_until IS NOT NULL AND lease_until < strftime('%s','now')-300"
            ),
            "verified_without_capsule": "SELECT id FROM findings WHERE state='VERIFIED' AND capsule_id IS NULL",
            "orphan_capsules": (
                "SELECT c.id FROM capsules c LEFT JOIN findings f ON f.id=c.finding_id WHERE f.id IS NULL"
            ),
            "scope_violations": (
                "SELECT COUNT(*) n FROM task_events WHERE kind='scope' AND ok=0 "
                "AND detail_json LIKE '%VIOLATION%'"
            ),
        }
        out: dict[str, Any] = {}
        for name, sql in q.items():
            rows = self._conn.execute(sql).fetchall()
            out[name] = [dict(r) for r in rows] if rows and name != "scope_violations" else (
                (rows[0]["n"] if rows else 0) if name == "scope_violations" else []
            )
        return out


def _row_to_task(row: sqlite3.Row) -> Task:
    return Task(
        id=row["id"],
        engagement_id=row["engagement_id"],
        idem_key=row["idem_key"],
        profile_name=row["profile_name"],
        profile_hash=row["profile_hash"],
        asset_id=row["asset_id"],
        oracle_ids=json.loads(row["oracle_ids"]),
        state=TaskState(row["state"]),
        lease_owner=row["lease_owner"],
        lease_until=row["lease_until"],
        heartbeat_at=row["heartbeat_at"],
        attempt=row["attempt"],
        max_attempts=row["max_attempts"],
        error_class=row["error_class"],
        error_code=row["error_code"],
        budget=Budget(**json.loads(row["budget_json"])),
        result=json.loads(row["result_json"]) if row["result_json"] else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
