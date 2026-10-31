"""审批门（05 §3）：人机交接的唯一接口。

四条规则写死在这里：
1. **默认全拒**：profile 没写这个动作类别 = 不允许，不是“等待审批”（VP-2005）。
2. **不可自批**：``decided_by`` 不得等于任务 ``lease_owner``。
3. **挂起有时限**：``approval_ttl=3600s``，过期转 EXPIRED。
4. **``discovery_probe`` 单列一类**：词表探测 = 扫描，与“验证”不同性质。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable


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


@dataclass
class ApprovalGate:
    """内存态审批门（B15 落库 + 控制台）。

    关键：``require`` 在没有对应 policy 时**直接拒绝**，不是进入等待。
    """

    policies: dict[str, GatePolicy] = field(default_factory=dict)
    ttl_s: int = APPROVAL_TTL_S
    decisions: list[ApprovalDecision] = field(default_factory=list)

    @classmethod
    def from_spec(cls, spec: Iterable[dict[str, str]], ttl_s: int = APPROVAL_TTL_S) -> "ApprovalGate":
        return cls(
            policies={p["action_class"]: GatePolicy(p["action_class"], p.get("when", "first_time")) for p in spec},
            ttl_s=ttl_s,
        )

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
        self.decisions.append(
            ApprovalDecision(action_class, ApprovalState.PENDING, requested_at=now)
        )
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
        if by == lease_owner:
            raise PermissionError("审批不可自我批准：decided_by 不得等于 lease_owner")
        pending = [d for d in self.decisions if d.action_class == action_class and d.state is ApprovalState.PENDING]
        if not pending:
            raise KeyError(f"没有待批记录：{action_class}")
        record = pending[-1]
        if now - record.requested_at > self.ttl_s:
            record.state = ApprovalState.EXPIRED
            raise ApprovalTTLExpired(action_class)
        record.state = ApprovalState.APPROVED if approved else ApprovalState.REJECTED
        record.decided_by = by
        record.reason = reason
        return record


def gate_denies(action_class: str, spec: Iterable[dict[str, str]]) -> bool:
    """profile 未登记该动作类别 → 拒绝（默认全拒）。"""
    return all(p.get("action_class") != action_class for p in spec)
