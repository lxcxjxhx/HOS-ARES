"""出网工具（27 N1）：``send`` 只接受 ``RouteBinding``，且必须已安装 GuardContext。

B3 起 ``send`` 的前两步固定是 ② 绑定门与 ③ preflight（05 §1.0 硬约束 1）；
真实传输仍在 B10 接线，但**闸门已经挡在传输之前**。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Mapping

from ..models import HttpTrace
from ..routes.types import RouteBinding

if TYPE_CHECKING:  # 只用于类型，不在运行时引入 guard（避免循环 import）
    from ..guard.context import GuardContext


class TransportUnavailable(RuntimeError):
    """传输实现尚未接线（B10）。"""


_GUARD: "GuardContext | None" = None


def install_guard(ctx: "GuardContext") -> None:
    """安装本次请求的闸门上下文（worker 每任务装一次）。"""
    global _GUARD
    _GUARD = ctx


def uninstall_guard() -> None:
    global _GUARD
    _GUARD = None


def installed_guard() -> "GuardContext | None":
    return _GUARD


def send(
    binding: RouteBinding,
    params: Mapping[str, str],
    auth: str | None = None,
) -> HttpTrace:
    """唯一签名。传字符串即 ``TypeError``；没有闸门即拒绝；闸门不过即拒绝。"""
    if not isinstance(binding, RouteBinding):
        raise TypeError(
            f"send() 只接受 RouteBinding，收到 {type(binding).__name__}；"
            "地址必须由 route_discovery 解析（27 N1）"
        )

    ctx = _GUARD
    if ctx is None:
        from ..guard.context import GuardNotInstalled

        raise GuardNotInstalled()

    ctx.binding_gate(binding)  # ② 绑定门（来源越界即抛）
    ctx.preflight()            # ③ 预算 / 令牌 / 审批

    raise TransportUnavailable("HTTP 传输在 B10 接线；闸门已在传输之前执行")
