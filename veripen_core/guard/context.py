"""GuardContext：把四处校验、令牌桶、预算、审批绑成一次请求的前置闸。

``tools.http.send`` 内部持有它，调用方无法跳过（05 §1.0 硬约束 1：
**没有 skip_guard 参数**）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..routes.types import RouteBinding, RouteSet
from .approval import ApprovalGate, ApprovalRequired
from .budget import Budget
from .errors import GuardError, ScopeDenied
from .ratelimit import TokenBucket
from .scope import PlanDecision, ScopeGuard


class GuardNotInstalled(GuardError):
    """未安装 GuardContext 就调用 send：拒绝出网（fail-closed）。"""

    code = "VP-2000"

    def __init__(self) -> None:
        super().__init__("未安装 GuardContext：任何出网必须先过四处校验")


@dataclass
class GuardContext:
    scope: ScopeGuard
    routeset: RouteSet
    plan: PlanDecision
    bucket: TokenBucket = field(default_factory=TokenBucket)
    budget: Budget = field(default_factory=Budget)
    approval: ApprovalGate | None = None
    oracle_id: str = ""
    approval_action: str | None = None
    now: int = 0

    # ── ② 绑定门 ────────────────────────────────────────────────
    def binding_gate(self, binding: RouteBinding) -> RouteBinding:
        return self.scope.binding(binding.ref, self.routeset)

    # ── ③ 执行前 ────────────────────────────────────────────────
    def preflight(self) -> None:
        if not self.plan.is_allowed(self.oracle_id):
            raise ScopeDenied("plan_denied", oracle=self.oracle_id)
        self.budget.check()
        self.bucket.acquire()
        if self.approval_action is not None:
            if self.approval is None:
                from .errors import NoApprovalGate

                raise NoApprovalGate(self.approval_action)
            self.approval.require(self.approval_action, now=self.now)
        self.budget.spend_request()

    def events(self) -> list[dict[str, object]]:
        return self.scope.events


__all__ = ["ApprovalRequired", "GuardContext", "GuardNotInstalled"]
