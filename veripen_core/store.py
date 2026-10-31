"""SQLite 存储层（B1：三张核心表 tasks / findings / capsules）。

迁移唯一入口是 ``transition``：带 ``WHERE state=?`` 的条件更新，返回是否抢到（03 §2）。
幂等唯一入口是 ``submit_task``：``idem_key`` 唯一索引，重复提交返回已有任务。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .models import (
    Capsule,
    Finding,
    FindingState,
    ReplayStatus,
    TaskState,
    idem_key,
    new_ulid,
)

DDL = """
CREATE TABLE IF NOT EXISTS tasks (
  id              TEXT PRIMARY KEY,
  engagement_id   TEXT NOT NULL,
  idem_key        TEXT NOT NULL,
  profile_name    TEXT NOT NULL,
  profile_hash    TEXT NOT NULL,
  asset_id        TEXT NOT NULL,
  oracle_ids      TEXT NOT NULL,
  state           TEXT NOT NULL DEFAULT 'QUEUED',
  lease_owner     TEXT,
  lease_until     INTEGER,
  heartbeat_at    INTEGER,
  attempt         INTEGER NOT NULL DEFAULT 0,
  max_attempts    INTEGER NOT NULL DEFAULT 3,
  error_class     TEXT,
  error_code      TEXT,
  budget_json     TEXT NOT NULL DEFAULT '{}',
  routeset_sha256 TEXT,
  result_json     TEXT,
  created_at      INTEGER NOT NULL,
  updated_at      INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_tasks_idem ON tasks(idem_key);
CREATE INDEX IF NOT EXISTS ix_tasks_pick ON tasks(state, lease_until);

CREATE TABLE IF NOT EXISTS findings (
  id             TEXT PRIMARY KEY,
  task_id        TEXT NOT NULL,
  asset_id       TEXT NOT NULL,
  oracle_id      TEXT NOT NULL,
  oracle_version TEXT NOT NULL,
  title          TEXT NOT NULL,
  severity       TEXT NOT NULL,
  confidence     REAL NOT NULL,
  route_strength TEXT,
  cwe            TEXT,
  state          TEXT NOT NULL,
  capsule_id     TEXT,
  created_at     INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_finding_run ON findings(task_id, oracle_id, asset_id);

CREATE TABLE IF NOT EXISTS capsules (
  id              TEXT PRIMARY KEY,
  finding_id      TEXT NOT NULL,
  path            TEXT NOT NULL,
  self_sha256     TEXT NOT NULL,
  prev_sha256     TEXT,
  routeset_sha256 TEXT,
  route_ref       TEXT,
  sealed_at       INTEGER NOT NULL,
  replay_status   TEXT NOT NULL DEFAULT 'NEVER',
  replay_at       INTEGER
);
"""


def now() -> int:
    return int(time.time())


class Store:
    """薄封装：所有写操作都是显式 SQL，不做 ORM 魔法。"""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        # 跨线程访问：调度器与心跳线程共用一个连接，写操作串行化（B2）
        self.lock = threading.RLock()

    # ── 连接 ────────────────────────────────────────────────────
    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def init(self) -> "Store":
        self.conn.executescript(DDL)
        self.conn.commit()
        return self

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ── tasks ───────────────────────────────────────────────────
    def submit_task(
        self,
        *,
        engagement_id: str,
        profile_name: str,
        profile_hash: str,
        asset_id: str,
        oracle_ids: list[str],
        params: dict[str, Any] | None = None,
        max_attempts: int = 3,
    ) -> tuple[str, bool]:
        """建任务（幂等）。返回 ``(task_id, reused)``。"""
        key = idem_key(engagement_id, asset_id, "|".join(sorted(oracle_ids)), params)
        row = self.conn.execute("SELECT id FROM tasks WHERE idem_key=?", (key,)).fetchone()
        if row is not None:
            return str(row["id"]), True

        tid = new_ulid()
        ts = now()
        with self.lock:
            return self._insert_task(
                tid, key, engagement_id, profile_name, profile_hash, asset_id, oracle_ids, ts, max_attempts
            )

    def _insert_task(
        self,
        tid: str,
        key: str,
        engagement_id: str,
        profile_name: str,
        profile_hash: str,
        asset_id: str,
        oracle_ids: list[str],
        ts: int,
        max_attempts: int = 3,
    ) -> tuple[str, bool]:
        try:
            self.conn.execute(
                "INSERT INTO tasks (id, engagement_id, idem_key, profile_name, profile_hash,"
                " asset_id, oracle_ids, state, created_at, updated_at, max_attempts)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    tid,
                    engagement_id,
                    key,
                    profile_name,
                    profile_hash,
                    asset_id,
                    json.dumps(sorted(oracle_ids), ensure_ascii=False),
                    TaskState.QUEUED.value,
                    ts,
                    ts,
                    max_attempts,
                ),
            )
            self.conn.commit()
            return tid, False
        except sqlite3.IntegrityError:
            # 并发下被别的进程先插入：返回既有任务（同一 idem_key 只打一次目标）
            self.conn.rollback()
            row = self.conn.execute(
                "SELECT id FROM tasks WHERE idem_key=?", (key,)
            ).fetchone()
            if row is None:  # pragma: no cover - 唯一索引保证不会发生
                raise
            return str(row["id"]), True

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row is not None else None

    def transition(
        self, task_id: str, frm: TaskState, to: TaskState, **fields: Any
    ) -> bool:
        """条件状态迁移；0 行说明被抢，调用方必须放弃当前工作（03 §2）。"""
        sets = "state=?, updated_at=?"
        values: list[Any] = [to.value, now()]
        for k, v in fields.items():
            sets += f", {k}=?"
            values.append(v)
        values.extend([task_id, frm.value])
        with self.lock:
            cur = self.conn.execute(
                f"UPDATE tasks SET {sets} WHERE id=? AND state=?", values
            )
            self.conn.commit()
        return cur.rowcount == 1

    def count_by_state(self, state: TaskState) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM tasks WHERE state=?", (state.value,)
        ).fetchone()
        return int(row["c"])

    # ── findings ────────────────────────────────────────────────
    def record_finding(self, finding: Finding) -> str:
        fid = finding.id or new_ulid()
        self.conn.execute(
            "INSERT INTO findings (id, task_id, asset_id, oracle_id, oracle_version, title,"
            " severity, confidence, route_strength, cwe, state, capsule_id, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                fid,
                finding.task_id,
                finding.asset_id,
                finding.oracle_id,
                finding.oracle_version,
                finding.title,
                finding.severity,
                finding.confidence,
                finding.route_strength,
                finding.cwe,
                finding.state.value,
                finding.capsule_id,
                finding.created_at or now(),
            ),
        )
        self.conn.commit()
        return fid

    def get_finding(self, finding_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM findings WHERE id=?", (finding_id,)
        ).fetchone()
        return dict(row) if row is not None else None

    def verified_without_capsule(self) -> list[str]:
        """一致性自检 3：VERIFIED 必须有 capsule（03 §7）。"""
        rows = self.conn.execute(
            "SELECT id FROM findings WHERE state=? AND capsule_id IS NULL",
            (FindingState.VERIFIED.value,),
        ).fetchall()
        return [str(r["id"]) for r in rows]

    # ── capsules ────────────────────────────────────────────────
    def record_capsule(self, cap: Capsule, path: str) -> str:
        if not cap.self_sha256:
            raise ValueError("capsule 未 seal：self_sha256 为空不许入库")
        self.conn.execute(
            "INSERT OR REPLACE INTO capsules (id, finding_id, path, self_sha256,"
            " prev_sha256, routeset_sha256, route_ref, sealed_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                cap.self_sha256,
                cap.finding_id,
                path,
                cap.self_sha256,
                cap.prev_sha256,
                cap.routeset_sha256,
                cap.route_ref,
                cap.sealed_at or now(),
            ),
        )
        self.conn.commit()
        return cap.self_sha256

    def record_replay(self, capsule_id: str, status: ReplayStatus, at: int | None = None) -> None:
        """回写 replay 结果（06 §5 步骤 7）。不改 capsule 文件——证据只增不改。"""
        with self.lock:
            self.conn.execute(
                "UPDATE capsules SET replay_status=?, replay_at=? WHERE id=?",
                (status.value if isinstance(status, ReplayStatus) else str(status), at or now(), capsule_id),
            )
            self.conn.commit()

    def get_capsule(self, capsule_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM capsules WHERE id=?", (capsule_id,)).fetchone()
        return dict(row) if row is not None else None
