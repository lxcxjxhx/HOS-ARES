-- 001_init.sql · HOS-ARES 初始 schema（`03 §1` 逐字实现）
-- 只前进：不改已有列语义；新需求追加 002_*.sql。
-- SQLite 与 PostgreSQL 兼容：TEXT/INTEGER/REAL 通用，索引名一致。

CREATE TABLE IF NOT EXISTS tasks (
  id            TEXT PRIMARY KEY,
  engagement_id TEXT NOT NULL,
  idem_key      TEXT NOT NULL,
  profile_name  TEXT NOT NULL,
  profile_hash  TEXT NOT NULL,
  asset_id      TEXT NOT NULL,
  oracle_ids    TEXT NOT NULL,
  state         TEXT NOT NULL DEFAULT 'QUEUED',
  lease_owner   TEXT,
  lease_until   INTEGER,
  heartbeat_at  INTEGER,
  attempt       INTEGER NOT NULL DEFAULT 0,
  max_attempts  INTEGER NOT NULL DEFAULT 3,
  error_class   TEXT,
  error_code    TEXT,
  budget_json   TEXT NOT NULL,
  result_json   TEXT,
  created_at    INTEGER NOT NULL,
  updated_at    INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_tasks_idem ON tasks(idem_key);
CREATE INDEX IF NOT EXISTS ix_tasks_pick   ON tasks(state, lease_until);
CREATE INDEX IF NOT EXISTS ix_tasks_engage ON tasks(engagement_id, state);

CREATE TABLE IF NOT EXISTS task_events (
  seq         INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id     TEXT NOT NULL,
  ts          INTEGER NOT NULL,
  kind        TEXT NOT NULL,
  oracle_id   TEXT,
  tool        TEXT,
  elapsed_ms  INTEGER,
  tokens      INTEGER DEFAULT 0,
  ok          INTEGER NOT NULL,
  err_class   TEXT,
  detail_json TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_task ON task_events(task_id, seq);

CREATE TABLE IF NOT EXISTS findings (
  id             TEXT PRIMARY KEY,
  task_id        TEXT NOT NULL,
  asset_id       TEXT NOT NULL,
  oracle_id      TEXT NOT NULL,
  oracle_version TEXT NOT NULL,
  title          TEXT NOT NULL,
  severity       TEXT NOT NULL,
  confidence     REAL NOT NULL,
  cwe            TEXT,
  state          TEXT NOT NULL,
  capsule_id     TEXT,
  created_at     INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_finding_run ON findings(task_id, oracle_id, asset_id);

CREATE TABLE IF NOT EXISTS capsules (
  id            TEXT PRIMARY KEY,
  finding_id    TEXT NOT NULL,
  path          TEXT NOT NULL,
  self_sha256   TEXT NOT NULL,
  prev_sha256   TEXT,
  sealed_at     INTEGER NOT NULL,
  replay_status TEXT DEFAULT 'NEVER',
  replay_at     INTEGER
);

CREATE TABLE IF NOT EXISTS approvals (
  id           TEXT PRIMARY KEY,
  task_id      TEXT NOT NULL,
  action_class TEXT NOT NULL,
  impact_json  TEXT NOT NULL,
  state        TEXT NOT NULL DEFAULT 'PENDING',
  requested_at INTEGER NOT NULL,
  decided_at   INTEGER,
  decided_by   TEXT,
  reason       TEXT
);

CREATE TABLE IF NOT EXISTS bench_runs (
  id                  TEXT PRIMARY KEY,
  profile_name        TEXT NOT NULL,
  started_at          INTEGER NOT NULL,
  cases_total         INTEGER NOT NULL,
  tp INTEGER, fp INTEGER, fn INTEGER, tn INTEGER,
  precision REAL, recall REAL, f1 REAL,
  replay_pass         INTEGER,
  first_finding_steps INTEGER,
  tokens              INTEGER,
  cost_usd            REAL,
  wall_s              REAL,
  voided              INTEGER DEFAULT 0,
  notes               TEXT
);
