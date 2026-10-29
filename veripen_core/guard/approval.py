"""审批门（`05 §3`）：人机交接的唯一接口。

**默认全拒**：profile 不写就是不允许，不存在"默认放行"。
**审批不可自我批准**：`decided_by` 不得等于 `lease_owner`（写死校验）。

流程：
    agent 想执行 action_class=X
      → require(task, X, impact)
          ├─ gates 里没有 X                → 直接拒绝（不允许就是不允许）
          ├─ 有 X 且 when=first_time 且已批过 → 放行
          └─ 否则 → 任务转 AWAITING_APPROVAL，写 approvals(PENDING)，agent 挂起
                       ↓ POST /v1/approvals/{id} {decision, reason, by}
                   APPROVED → 恢复；REJECTED → 该动作 BLOCKED，任务继续其余 oracle
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from ..errors import ERR_APPROVAL_REQUIRED, VeripenError
from ..models import Approval, ApprovalState, Task, TaskState, new_ulid
from ..store import Store

#: 挂起时限（`05 §3`）：过期转 EXPIRED → 该动作 BLOCKED
APPROVAL_TTL_S = 3600


class GateWhen(str, Enum):
    ALWAYS = "always"
    FIRST_TIME = "first_time"


@dataclass(frozen=True)
class ApprovalGate:
    """`profile.policy.approval_gates[]` 的一条。"""

    action_class: str
    when: GateWhen = GateWhen.ALWAYS
    approver_role: str = "secops_lead"


class ApprovalOutcome(str, Enum):
    """三态：放行 / 需挂起 / 硬拒绝。"""

    ALLOWED = "allowed"
    PENDING = "pending"
    DENIED = "denied"


@dataclass
class ApprovalResult:
    outcome: ApprovalOutcome
    approval_id: str | None = None
    reason: str = ""


@dataclass
class ApprovalGuard:
    """审批门。所有状态落在 `approvals` 表，进程重启后可续。"""

    store: Store
    gates: tuple[ApprovalGate, ...] = ()
    ttl_s: int = APPROVAL_TTL_S
    #: 可注入时钟（测试推进时间用）。默认墙钟。
    clock: Callable[[], float] = time.time

    def _gate_for(self, action_class: str) -> ApprovalGate | None:
        for g in self.gates:
            if g.action_class == action_class:
                return g
        return None

    def require(
        self,
        *,
        task: Task,
        action_class: str,
        impact: dict | None = None,
    ) -> ApprovalResult:
        """请求执行一个受控动作。

        返回 `DENIED` 时调用方必须把该动作标 BLOCKED 并继续其余 work——
        不是抛异常中断整个任务（`05 §3` REJECTED 语义）。
        """
        gate = self._gate_for(action_class)
        if gate is None:
            # 默认全拒：没写就是不允许
            return ApprovalResult(
                outcome=ApprovalOutcome.DENIED,
                reason=f"action_class {action_class!r} has no approval gate in profile; denied by default",
            )

        if gate.when is GateWhen.FIRST_TIME and self._already_approved(task.engagement_id, action_class):
            return ApprovalResult(outcome=ApprovalOutcome.ALLOWED, reason="first_time gate already approved")

        # 已有 PENDING 的直接复用，不重复建单
        existing = self._pending_for(task.id, action_class)
        if existing is not None:
            return ApprovalResult(
                outcome=ApprovalOutcome.PENDING,
                approval_id=existing["id"],
                reason="already awaiting approval",
            )

        now = int(self.clock())
        approval = Approval(
            id=new_ulid(now * 1000),
            task_id=task.id,
            action_class=action_class,
            impact=impact or {},
            state=ApprovalState.PENDING,
            requested_at=now,
        )
        self.store.insert_approval(approval)

        # 任务转 AWAITING_APPROVAL（`03 §2` 状态机）
        if task.state is TaskState.RUNNING:
            self.store.transition(task.id, TaskState.RUNNING, TaskState.AWAITING_APPROVAL, now=now)

        return ApprovalResult(
            outcome=ApprovalOutcome.PENDING,
            approval_id=approval.id,
            reason=f"action_class {action_class!r} requires {gate.approver_role} approval",
        )

    def decide(
        self,
        *,
        approval_id: str,
        decision: str,
        by: str,
        reason: str = "",
    ) -> ApprovalState:
        """人工审批决定。

        **不可自我批准**（`05 §3` 写死校验）：`by` 不得等于该任务的 `lease_owner`。
        """
        row = self.store._conn.execute(  # noqa: SLF001 — 同包内部
            "SELECT a.*, t.lease_owner, t.id AS task_id2 FROM approvals a "
            "JOIN tasks t ON t.id = a.task_id WHERE a.id=?",
            (approval_id,),
        ).fetchone()
        if row is None:
            raise VeripenError("VP-1001", f"approval not found: {approval_id}", detail={"approval_id": approval_id})

        lease_owner = row["lease_owner"]
        if lease_owner and by == lease_owner:
            raise VeripenError(
                ERR_APPROVAL_REQUIRED,
                "self-approval forbidden: decided_by must differ from lease_owner",
                detail={"approval_id": approval_id, "by": by, "lease_owner": lease_owner},
                hint="审批必须由任务执行者之外的人做出",
            )

        state = ApprovalState.APPROVED if decision == "approved" else ApprovalState.REJECTED
        ok = self.store.decide_approval(approval_id, state, by, reason)
        if not ok:
            raise VeripenError(
                "VP-1001",
                f"approval {approval_id} is not PENDING",
                detail={"approval_id": approval_id},
            )

        # 恢复任务（APPROVED）或让它继续走（REJECTED 也恢复 RUNNING，由调用方标 BLOCKED）
        task_id = row["task_id"]
        now = int(self.clock())
        if state is ApprovalState.APPROVED:
            self.store.transition(task_id, TaskState.AWAITING_APPROVAL, TaskState.RUNNING, now=now)
        else:
            self.store.transition(task_id, TaskState.AWAITING_APPROVAL, TaskState.RUNNING, now=now)
        return state

    def expire_stale(self) -> list[str]:
        """把超时的 PENDING 转 EXPIRED（`05 §3` approval_ttl=3600s）。"""
        now = int(self.clock())
        cutoff = now - self.ttl_s
        rows = self.store._conn.execute(  # noqa: SLF001
            "SELECT id FROM approvals WHERE state='PENDING' AND requested_at < ?", (cutoff,)
        ).fetchall()
        expired: list[str] = []
        for r in rows:
            ok = self.store.decide_approval(r["id"], ApprovalState.EXPIRED, "system", "ttl expired", now=now)
            if ok:
                expired.append(r["id"])
        return expired

    def pending(self, *, task_id: str | None = None) -> list[dict]:
        if task_id:
            rows = self.store._conn.execute(  # noqa: SLF001
                "SELECT * FROM approvals WHERE state='PENDING' AND task_id=? ORDER BY requested_at",
                (task_id,),
            ).fetchall()
        else:
            rows = self.store._conn.execute(  # noqa: SLF001
                "SELECT * FROM approvals WHERE state='PENDING' ORDER BY requested_at"
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------- 内部 ----------

    def _already_approved(self, engagement_id: str, action_class: str) -> bool:
        row = self.store._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) n FROM approvals a JOIN tasks t ON t.id=a.task_id "
            "WHERE t.engagement_id=? AND a.action_class=? AND a.state='APPROVED'",
            (engagement_id, action_class),
        ).fetchone()
        return bool(row and row["n"] > 0)

    def _pending_for(self, task_id: str, action_class: str):
        return self.store._conn.execute(  # noqa: SLF001
            "SELECT * FROM approvals WHERE task_id=? AND action_class=? AND state='PENDING' LIMIT 1",
            (task_id, action_class),
        ).fetchone()
