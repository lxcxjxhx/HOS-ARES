"""guard 包：scope 四处校验 / 令牌桶 / 审批门。

铁律（05 §1.0）：
- ②③ 必须由 ``tools.http.send`` 内部调用，调用方无法跳过（没有 ``skip_guard``）。
- ④ 必须挂在 transport 层：只有那里拿得到“实际连了谁”。
- 门失败一律抛异常，**不降级、不尽力而为**。
"""

from __future__ import annotations

from .approval import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalRequired,
    ApprovalState,
    ApprovalTTLExpired,
    gate_denies,
)
from .budget import Budget, BudgetExhausted
from .errors import (
    GuardError,
    ImpactExceeded,
    NoApprovalGate,
    ScopeDenied,
    ScopeViolation,
    ThrottleTimeout,
)
from ..routes.types import RouteStale, RouteTooWeak, UnboundRoute
from .ratelimit import TokenBucket, ThrottleConfig
from .scope import (
    AssetScope,
    GuardDecision,
    PlanDecision,
    ScopeGuard,
    check_binding,
    check_plan,
    check_post_connect,
    check_preflight,
)

__all__ = [
    "ApprovalDecision",
    "ApprovalGate",
    "ApprovalRequired",
    "ApprovalState",
    "ApprovalTTLExpired",
    "AssetScope",
    "Budget",
    "BudgetExhausted",
    "GuardDecision",
    "GuardError",
    "ImpactExceeded",
    "NoApprovalGate",
    "PlanDecision",
    "RouteStale",
    "RouteTooWeak",
    "UnboundRoute",
    "ScopeDenied",
    "ScopeGuard",
    "ScopeViolation",
    "ThrottleConfig",
    "ThrottleTimeout",
    "TokenBucket",
    "check_binding",
    "check_plan",
    "check_post_connect",
    "check_preflight",
    "gate_denies",
]
