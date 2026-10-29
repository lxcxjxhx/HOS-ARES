"""核心数据模型（`03 §1` + `24 §3` 的对外形状）。

只做两件事：定义形状、做不变量检查。所有持久化在 `store.py`。
"""

from __future__ import annotations

import hashlib
import re
import time
import uuid
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .oracle_api import FindingState, Severity, Verdict, canonical_json, sha256_of


class TaskState(str, Enum):
    """任务 8 态（`03 §2`）。迁移只走 `store.transition`。"""

    QUEUED = "QUEUED"
    LEASED = "LEASED"
    RUNNING = "RUNNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    DONE = "DONE"
    FAILED = "FAILED"
    DEAD_LETTER = "DEAD_LETTER"
    CANCELLED = "CANCELLED"


#: 合法迁移表。任何不在表里的迁移都是编程错误，`transition` 直接抛。
ALLOWED_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.QUEUED: frozenset({TaskState.LEASED, TaskState.CANCELLED, TaskState.DEAD_LETTER}),
    TaskState.LEASED: frozenset({TaskState.RUNNING, TaskState.QUEUED, TaskState.DEAD_LETTER, TaskState.CANCELLED}),
    TaskState.RUNNING: frozenset(
        {
            TaskState.AWAITING_APPROVAL,
            TaskState.DONE,
            TaskState.FAILED,
            TaskState.QUEUED,
            TaskState.DEAD_LETTER,
            TaskState.CANCELLED,
        }
    ),
    TaskState.AWAITING_APPROVAL: frozenset({TaskState.RUNNING, TaskState.FAILED, TaskState.CANCELLED}),
    TaskState.FAILED: frozenset({TaskState.QUEUED, TaskState.DEAD_LETTER}),
    TaskState.DONE: frozenset(),
    TaskState.DEAD_LETTER: frozenset({TaskState.QUEUED}),
    TaskState.CANCELLED: frozenset(),
}

#: 心跳 5s / TTL 30s / 扫描 5s / 重试 3 次（`03 §3`、`15 §C`）
HEARTBEAT_INTERVAL_S = 5
LEASE_TTL_S = 30
RECLAIM_SCAN_S = 5
MAX_ATTEMPTS = 3
BACKOFF_SCHEDULE_S = (1.0, 4.0, 16.0)

#: 预算（`15 §C`）
DEFAULT_MAX_STEPS = 120
DEFAULT_MAX_TOKENS = 400_000
DEFAULT_MAX_WALL_CLOCK_S = 1800
DEFAULT_MAX_COST_USD = 5.0
BUDGET_CONVERGE_RATIO = 0.8

_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_ulid(ts_ms: int | None = None) -> str:
    """单调 ULID（Crockford base32，26 字符）。无第三方依赖，时钟可注入以便测试。"""
    ts = int(time.time() * 1000) if ts_ms is None else int(ts_ms)
    rand = uuid.uuid4().int
    chars: list[str] = []
    for i in range(10):
        chars.append(_ULID_ALPHABET[(ts >> (5 * (9 - i))) & 0x1F])
    for i in range(16):
        chars.append(_ULID_ALPHABET[(rand >> (5 * (15 - i))) & 0x1F])
    return "".join(chars)


def idem_key(
    *,
    engagement_id: str,
    asset_id: str,
    oracle_id: str,
    params: dict[str, Any] | None = None,
) -> str:
    """`sha256(engagement|asset|oracle|params_canonical)`（`01 §6`）。"""
    return sha256_of(
        {
            "engagement_id": engagement_id,
            "asset_id": asset_id,
            "oracle_id": oracle_id,
            "params": params or {},
        }
    )


class Budget(BaseModel):
    """四预算（`08` §四预算）。消耗与上限分开存，便于 80% 收敛判断。"""

    model_config = ConfigDict(extra="forbid")

    max_steps: int = DEFAULT_MAX_STEPS
    max_tokens: int = DEFAULT_MAX_TOKENS
    max_wall_clock_s: int = DEFAULT_MAX_WALL_CLOCK_S
    max_cost_usd: float = DEFAULT_MAX_COST_USD

    used_steps: int = 0
    used_tokens: int = 0
    used_wall_clock_s: float = 0.0
    used_cost_usd: float = 0.0

    def exhausted(self) -> bool:
        return (
            self.used_steps >= self.max_steps
            or self.used_tokens >= self.max_tokens
            or self.used_wall_clock_s >= self.max_wall_clock_s
            or self.used_cost_usd >= self.max_cost_usd
        )

    def should_converge(self) -> bool:
        """到 80% 就准备收敛，而不是撞墙才停（`15 §C`）。"""
        return (
            self.used_steps >= self.max_steps * BUDGET_CONVERGE_RATIO
            or self.used_tokens >= self.max_tokens * BUDGET_CONVERGE_RATIO
            or self.used_wall_clock_s >= self.max_wall_clock_s * BUDGET_CONVERGE_RATIO
            or self.used_cost_usd >= self.max_cost_usd * BUDGET_CONVERGE_RATIO
        )


class TaskCreateRequest(BaseModel):
    """`POST /v1/tasks` 请求体（`24 §2.1`）。"""

    model_config = ConfigDict(extra="forbid")

    engagement_id: str = Field(min_length=1)
    profile: str = Field(min_length=1)
    asset_ids: list[str] = Field(min_length=1)
    oracle_ids: list[str] = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)


class TaskCreateResponse(BaseModel):
    """`POST /v1/tasks` 响应体。重复提交返回 200 + 已有对象（`24 §1`）。"""

    task_id: str
    state: TaskState
    idem_key: str
    reused: bool
    created_at: str


class Task(BaseModel):
    """`tasks` 表的内存形状（`03 §1`）。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    engagement_id: str
    idem_key: str
    profile_name: str
    profile_hash: str
    asset_id: str
    oracle_ids: list[str]
    state: TaskState = TaskState.QUEUED
    lease_owner: str | None = None
    lease_until: int | None = None
    heartbeat_at: int | None = None
    attempt: int = 0
    max_attempts: int = MAX_ATTEMPTS
    error_class: str | None = None
    error_code: str | None = None
    budget: Budget = Field(default_factory=Budget)
    result: dict[str, Any] | None = None
    created_at: int = 0
    updated_at: int = 0

    @property
    def is_terminal(self) -> bool:
        return self.state in (TaskState.DONE, TaskState.CANCELLED)


class Finding(BaseModel):
    """`findings` 表（`03 §1`）。

    **硬约束**：`state=VERIFIED` 必须有 `capsule_id`，否则构造即失败。
    这条约束在模型层就挡住，不等到报告渲染（`03 §4`）。
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    task_id: str
    asset_id: str
    oracle_id: str
    oracle_version: str
    title: str
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)
    cwe: str | None = None
    state: FindingState
    capsule_id: str | None = None
    created_at: int = 0

    @model_validator(mode="after")
    def _verified_requires_capsule(self) -> Finding:
        if self.state is FindingState.VERIFIED and not self.capsule_id:
            raise ValueError("VERIFIED finding must reference a capsule_id (03 §4 hard constraint)")
        return self


class CapsuleMeta(BaseModel):
    """`capsules` 表的索引部分；正文在文件里（`03 §6` 归档策略）。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    finding_id: str
    path: str
    self_sha256: str
    prev_sha256: str | None = None
    sealed_at: int = 0
    replay_status: Literal["NEVER", "OK", "FAILED", "BLOCKED"] = "NEVER"
    replay_at: int | None = None


class ApprovalState(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    task_id: str
    action_class: str
    impact: dict[str, Any] = Field(default_factory=dict)
    state: ApprovalState = ApprovalState.PENDING
    requested_at: int = 0
    decided_at: int | None = None
    decided_by: str | None = None
    reason: str | None = None


class TaskEvent(BaseModel):
    """`task_events` 行。`detail_json` 只存 <=2KB（`03 §6`）。"""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    ts: int
    kind: str
    ok: bool
    oracle_id: str | None = None
    tool: str | None = None
    elapsed_ms: int | None = None
    tokens: int = 0
    err_class: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, v: str) -> str:
        allowed = {
            "plan",
            "preflight",
            "http",
            "oracle",
            "judge",
            "capsule",
            "budget",
            "scope",
            "throttle",
            "approval",
            "error",
        }
        if v not in allowed:
            raise ValueError(f"unknown task_event kind: {v} (03 §1)")
        return v

    def detail_size(self) -> int:
        return len(canonical_json(self.detail).encode("utf-8"))


PROFILE_HASH_RE = re.compile(r"^sha256:[a-f0-9]{64}$")


def profile_hash(profile_doc: dict[str, Any]) -> str:
    """profile 全量规范化后哈希，进每条 capsule（`02 §1` #3）。"""
    return sha256_of(profile_doc)


def finding_id(*, task_id: str, oracle_id: str, asset_id: str) -> str:
    """`ux_finding_run` 的稳定 id：同任务同 oracle 同资产只有一条。"""
    digest = hashlib.sha256(f"{task_id}|{oracle_id}|{asset_id}".encode()).hexdigest()[:26]
    return "F" + digest.upper()


VERDICT_TO_FINDING_STATE: dict[Verdict, FindingState] = {
    Verdict.VERIFIED: FindingState.VERIFIED,
    Verdict.REFUTED: FindingState.REFUTED,
    Verdict.INCONCLUSIVE: FindingState.UNVERIFIABLE,
    Verdict.BLOCKED: FindingState.BLOCKED,
}
