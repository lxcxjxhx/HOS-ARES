"""VeriPen 内核数据模型与接口契约（B1 冻结面）。

设计要点（plan-veripen 01 §3）：
- 判定是纯函数：``Oracle.judge`` 只接收 ``RequestPair`` 列表，没有 ctx / 时钟 / 网络。
- 地址是运行时解析结果：``RouteCall`` 只有不透明 ``RouteRef``，类型上写不出 url/path/host/port。
- capsule 内容寻址：``seal_capsule`` 对规范化 JSON 求 sha256，二次生成字节相同。
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field, model_validator

# ── 枚举 ────────────────────────────────────────────────────────────


class ImpactClass(str, Enum):
    READ_ONLY = "read_only"
    REVERSIBLE_WRITE = "reversible_write"  # 本期不启用


class Verdict(str, Enum):
    VERIFIED = "VERIFIED"
    REFUTED = "REFUTED"
    INCONCLUSIVE = "INCONCLUSIVE"
    BLOCKED = "BLOCKED"


class TaskState(str, Enum):
    QUEUED = "QUEUED"
    LEASED = "LEASED"
    RUNNING = "RUNNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    DEAD_LETTER = "DEAD_LETTER"


class FindingState(str, Enum):
    CANDIDATE = "CANDIDATE"
    VERIFIED = "VERIFIED"
    REFUTED = "REFUTED"
    UNVERIFIABLE = "UNVERIFIABLE"
    BLOCKED = "BLOCKED"


class Outcome(str, Enum):
    """正交于 state：state 说结论，outcome 说这次跑成了没有（32 §3 A1）。"""

    REPORTED = "reported"
    UNDETERMINED = "undetermined"
    ERROR = "error"
    NOT_RUN = "not_run"


class CoverageSrc(str, Enum):
    MACHINE_OBSERVED = "machine_observed"
    AGENT_REPORTED = "agent_reported"
    NONE = "none"


class ReplayStatus(str, Enum):
    NEVER = "NEVER"
    OK = "OK"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    ROUTE_UNRESOLVED = "ROUTE_UNRESOLVED"
    ROUTE_DRIFT = "ROUTE_DRIFT"


# ── 纯数据契约（无地址字段）────────────────────────────────────────


@dataclass(frozen=True)
class RouteRef:
    """不透明绑定引用（route://<source>/<opaque16>）。不含任何可定位文本。"""

    token: str


@dataclass(frozen=True)
class HttpTrace:
    """一次请求的可复现记录（route 块由 evidence 层组装）。"""

    route_ref: str
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body_sha256: str = ""


@dataclass(frozen=True)
class RouteCall:
    """oracle 想发的一次请求：对哪个已解析绑定、改哪些参数、用谁的身份。

    注意：没有 url / path / host / port 字段 —— 类型上写不出来（27 N1/N2）。
    """

    ref: RouteRef
    params: dict[str, str]
    role: str
    method_override: str | None = None


@dataclass(frozen=True)
class RequestPair:
    variant: str
    baseline: HttpTrace
    probe: HttpTrace


@dataclass(frozen=True)
class Judgement:
    verdict: Verdict
    reason: str
    assertion: dict[str, Any]
    confidence: float
    route_strength: str


class OracleParams(BaseModel):
    """oracle 参数基类；具体 oracle 覆盖。"""

    model_config = {"extra": "forbid"}


@runtime_checkable
class Oracle(Protocol):
    """验证器协议。judge 必须是纯函数（无 IO/时钟/随机）。"""

    id: str
    version: str
    cwe: str
    impact: ImpactClass
    requires: list[str]
    params_schema: type[BaseModel]
    selector_tags: list[str]

    def applicable(self, ctx: Any) -> bool: ...

    def plan(self, ctx: Any, base: Any) -> list[RouteCall]: ...

    def judge(self, pairs: list[RequestPair]) -> Judgement: ...


# ── 持久化模型 ──────────────────────────────────────────────────────


class Task(BaseModel):
    id: str
    engagement_id: str
    idem_key: str
    profile_name: str
    profile_hash: str
    asset_id: str
    oracle_ids: list[str]
    state: TaskState = TaskState.QUEUED
    attempts: int = 0
    max_attempts: int = 3
    lease_owner: str | None = None
    lease_until: int | None = None
    routeset_sha256: str | None = None
    created_at: int = 0
    updated_at: int = 0


class Finding(BaseModel):
    id: str
    task_id: str
    asset_id: str
    oracle_id: str
    oracle_version: str
    title: str
    severity: str = "info"
    confidence: float = 0.0
    route_strength: str | None = None
    cwe: str | None = None
    state: FindingState = FindingState.CANDIDATE
    capsule_id: str | None = None
    outcome: Outcome = Outcome.REPORTED
    coverage_src: CoverageSrc = CoverageSrc.MACHINE_OBSERVED
    not_run_reason: str | None = None
    error_reason: str | None = None
    created_at: int = 0

    @model_validator(mode="after")
    def _outcome_consistent(self) -> "Finding":
        """三条自洽规则在模型层先炸一次（schema / DDL 是第二、第三道）。"""
        from .outcome import validate

        validate(
            outcome=self.outcome,
            state=self.state,
            coverage_src=self.coverage_src,
            error_reason=self.error_reason,
            not_run_reason=self.not_run_reason,
        )
        return self


class Capsule(BaseModel):
    finding_id: str
    oracle_id: str
    oracle_version: str
    verdict: Verdict
    assertion: dict[str, Any] = Field(default_factory=dict)
    routeset_sha256: str | None = None
    route_ref: str | None = None
    prev_sha256: str | None = None
    sealed_at: int = 0
    self_sha256: str = ""

    def without_sha(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"self_sha256"})


# ── 规范化与内容寻址 ────────────────────────────────────────────────


def canonical_json(obj: Any) -> bytes:
    """稳定序列化：键排序、紧凑分隔符、保留中文。同对象恒同字节。"""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def idem_key(
    engagement_id: str, asset_id: str, oracle_key: str, params: dict[str, Any] | None = None
) -> str:
    """任务幂等键：sha256(engagement|asset|oracle|params_canonical)（01 §6）。"""
    payload = {
        "engagement": engagement_id,
        "asset": asset_id,
        "oracle": oracle_key,
        "params": params or {},
    }
    return sha256_hex(canonical_json(payload))


def seal_capsule(cap: Capsule) -> Capsule:
    """内容寻址：返回带 self_sha256 的副本；同内容两次 seal 字节相同。"""
    digest = sha256_hex(canonical_json(cap.without_sha()))
    return cap.model_copy(update={"self_sha256": digest})


def new_ulid() -> str:
    """单调 id：时间前缀 + 随机后缀（用 secrets，不用可预测 random）。"""
    import secrets

    return f"{int(time.time() * 1000):013d}{secrets.token_hex(8)}"
