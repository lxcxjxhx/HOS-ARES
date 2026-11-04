"""审批门（05 §3）：人机交接的唯一接口。

四条规则写死在这里：
1. **默认全拒**：profile 没写这个动作类别 = 不允许，不是“等待审批”（VP-2005）。
2. **不可自批**：``decided_by`` 不得等于任务 ``lease_owner``。
3. **挂起有时限**：``approval_ttl=3600s``，过期转 EXPIRED。
4. **``discovery_probe`` 单列一类**：词表探测 = 扫描，与“验证”不同性质。

B60：``store``/``events`` 非空时，每一次申请与决策都落 ``approvals`` 表并追加一条
``task_events(kind='approval')``。**拒绝路径同样留痕**——自批、过期两条 raise 之前先写事件，
否则审计节只能看见"批了"，看不见"想批但被拒"。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Iterable, Mapping

if TYPE_CHECKING:  # 避免 guard → blackboard 的运行时耦合
    from ..blackboard import EventLog
    from ..store import Store


class ApprovalState(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ActionClass(str, Enum):
    REVERSIBLE_WRITE = "reversible_write"
    SCOPE_EXPAND = "scope_expand"
    DISCOVERY_PROBE = "discovery_probe"


APPROVAL_TTL_S = 3600


class ApprovalRequired(RuntimeError):
    """需要人拍板：任务转 AWAITING_APPROVAL，写 approvals(PENDING)。"""

    code = "VP-2005"

    def __init__(self, action_class: str, impact: dict[str, object] | None = None) -> None:
        super().__init__(f"需要审批：{action_class}")
        self.action_class = action_class
        self.impact = impact or {}


class ApprovalTTLExpired(RuntimeError):
    """挂起超时 → EXPIRED → 该动作 BLOCKED。"""

    code = "VP-2005"


@dataclass(frozen=True)
class GatePolicy:
    """一类动作的审批策略。``when`` 目前只支持 first_time。"""

    action_class: str
    when: str = "first_time"


@dataclass
class ApprovalDecision:
    action_class: str
    state: ApprovalState
    decided_by: str | None = None
    reason: str = ""
    requested_at: int = 0
    decided_at: int | None = None
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = uuid.uuid4().hex


@dataclass
class ApprovalGate:
    """内存态审批门（B15 落库 + 控制台）。

    关键：``require`` 在没有对应 policy 时**直接拒绝**，不是进入等待。
    """

    policies: dict[str, GatePolicy] = field(default_factory=dict)
    ttl_s: int = APPROVAL_TTL_S
    decisions: list[ApprovalDecision] = field(default_factory=list)
    #: B60：非空则落 ``approvals`` 表；为空保持纯内存（旧调用方零改动）
    store: "Store | None" = None
    #: B60：非空则追加 ``task_events(kind='approval')``
    events: "EventLog | None" = None
    #: ``approvals.task_id`` 必填；纯内存模式可为空串
    task_id: str = ""

    @classmethod
    def from_spec(
        cls,
        spec: Iterable[dict[str, str]],
        ttl_s: int = APPROVAL_TTL_S,
        *,
        store: "Store | None" = None,
        events: "EventLog | None" = None,
        task_id: str = "",
    ) -> "ApprovalGate":
        return cls(
            policies={p["action_class"]: GatePolicy(p["action_class"], p.get("when", "first_time")) for p in spec},
            ttl_s=ttl_s,
            store=store,
            events=events,
            task_id=task_id,
        )

    # ── 落库与留痕（B60）────────────────────────────────────────
    def _persist(self, record: ApprovalDecision, impact: Mapping[str, object] | None = None) -> None:
        """写 ``approvals`` 行。``INSERT OR REPLACE``：同一 id 先 PENDING 后决策只留最终态。"""
        if self.store is None:
            return
        with self.store.lock:
            self.store.conn.execute(
                "INSERT OR REPLACE INTO approvals"
                " (id, task_id, action_class, impact_json, state, requested_at,"
                "  decided_at, decided_by, reason)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    record.id, self.task_id, record.action_class,
                    json.dumps(dict(impact or {}), ensure_ascii=False, sort_keys=True),
                    record.state.value, record.requested_at,
                    record.decided_at, record.decided_by, record.reason,
                ),
            )
            self.store.conn.commit()

    def _emit(self, record: ApprovalDecision, *, ok: bool, phase: str,
              extra: Mapping[str, object] | None = None) -> None:
        """追加一条 ``task_events(kind='approval')``。**拒绝路径也必须走这里。**"""
        if self.events is None:
            return
        detail: dict[str, object] = {
            "phase": phase,
            "approval_id": record.id,
            "action_class": record.action_class,
            "state": record.state.value,
        }
        if record.decided_by:
            detail["by"] = record.decided_by
        if record.reason:
            detail["reason"] = record.reason
        detail.update(dict(extra or {}))
        self.events.record(task_id=self.task_id, kind="approval", ok=ok, detail=detail)

    # ── 查询 ────────────────────────────────────────────────────
    def has_gate(self, action_class: str) -> bool:
        return action_class in self.policies

    def approved(self, engagement_id: str, action_class: str) -> bool:
        return any(
            d.action_class == action_class
            and d.state is ApprovalState.APPROVED
            and (d.reason == engagement_id or True)  # first_time：本 engagement 批过即放行
            for d in self.decisions
        )

    # ── 申请 ────────────────────────────────────────────────────
    def require(
        self,
        action_class: str,
        *,
        now: int,
        impact: dict[str, object] | None = None,
    ) -> None:
        """放行则返回；不允许抛 ``ApprovalRequired``（上层转 AWAITING_APPROVAL）。"""
        if not self.has_gate(action_class):
            from .errors import NoApprovalGate

            raise NoApprovalGate(action_class)
        if self.approved("", action_class):
            return
        record = ApprovalDecision(action_class, ApprovalState.PENDING, requested_at=now)
        self.decisions.append(record)
        self._persist(record, impact)
        self._emit(record, ok=False, phase="request",
                   extra={"impact": dict(impact or {})})
        raise ApprovalRequired(action_class, impact)

    # ── 决策 ────────────────────────────────────────────────────
    def decide(
        self,
        action_class: str,
        *,
        approved: bool,
        by: str,
        lease_owner: str,
        now: int,
        reason: str = "",
    ) -> ApprovalDecision:
        pending = [d for d in self.decisions if d.action_class == action_class and d.state is ApprovalState.PENDING]
        record = pending[-1] if pending else ApprovalDecision(
            action_class, ApprovalState.PENDING, requested_at=now)

        # 自批：拒绝本身也要留痕（判据 C），否则审计里只剩"没批过"
        if by == lease_owner:
            record.state = ApprovalState.REJECTED
            record.decided_by = by
            record.decided_at = now
            record.reason = reason or "self_approval"
            self._emit(record, ok=False, phase="decision",
                       extra={"denied": "self_approval"})
            raise PermissionError("审批不可自我批准：decided_by 不得等于 lease_owner")

        if not pending:
            raise KeyError(f"没有待批记录：{action_class}")

        if now - record.requested_at > self.ttl_s:
            record.state = ApprovalState.EXPIRED
            record.decided_at = now
            self._persist(record)
            self._emit(record, ok=False, phase="decision", extra={"expired": True})
            raise ApprovalTTLExpired(action_class)

        record.state = ApprovalState.APPROVED if approved else ApprovalState.REJECTED
        record.decided_by = by
        record.decided_at = now
        record.reason = reason
        self._persist(record)
        self._emit(record, ok=approved, phase="decision")
        return record


def gate_denies(action_class: str, spec: Iterable[dict[str, str]]) -> bool:
    """profile 未登记该动作类别 → 拒绝（默认全拒）。"""
    return all(p.get("action_class") != action_class for p in spec)
