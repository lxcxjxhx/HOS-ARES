"""guard 的错误类型与错误码（24 §4 唯一清单，代码只许引用这里的常量）。

每个异常带 ``code`` 与 ``retryable``，让上层按 04 §3 的分类做决定，
而不是靠 ``except Exception`` 猜。
"""

from __future__ import annotations

# ── 错误码常量（与 24 §4 表一致）────────────────────────────────────
VP_ASSET_NOT_IN_SCOPE = "VP-2001"
VP_PEER_DENIED = "VP-2002"
VP_REDIRECT_OUT_OF_SCOPE = "VP-2003"
VP_IMPACT_EXCEEDED = "VP-2004"
VP_NO_APPROVAL_GATE = "VP-2005"
VP_ROUTE_UNBOUND = "VP-2006"
VP_BINDING_UNBOUND = "VP-2007"
VP_THROTTLE_TIMEOUT = "VP-3003"


class GuardError(RuntimeError):
    code: str = "VP-2000"
    retryable: bool = False


class ScopeDenied(GuardError):
    """①②③ 任一范围内的拒绝：不发包。"""

    code = VP_ASSET_NOT_IN_SCOPE

    def __init__(self, reason: str, **detail: object) -> None:
        super().__init__(f"{reason} {detail}")
        self.reason = reason
        self.detail = detail


class PlanDenied(ScopeDenied):
    """① 规划期拒绝。"""


class ImpactExceeded(GuardError):
    code = VP_IMPACT_EXCEEDED


class NoApprovalGate(GuardError):
    """profile 没有该动作类别的审批门 = 不允许（不是“等待审批”）。"""

    code = VP_NO_APPROVAL_GATE


# 绑定类异常只有一份，定义在路由层（27 §3.9），这里不再重复定义：
#   routes.types.UnboundRoute (VP-2007) / RouteTooWeak (VP-2006) / RouteStale (VP-2006, retryable)


class ScopeViolation(ScopeDenied):
    """④ 命中：实际对端不在解析集合。P0 事故。"""

    code = VP_PEER_DENIED

    def __init__(self, peer_ip: str, asset_id: str, reason: str = "peer_not_in_scope") -> None:
        super().__init__(reason, peer_ip=peer_ip, asset_id=asset_id)
        self.peer_ip = peer_ip
        self.asset_id = asset_id
        self.reason = reason


class ThrottleTimeout(GuardError):
    code = VP_THROTTLE_TIMEOUT
    retryable = True
