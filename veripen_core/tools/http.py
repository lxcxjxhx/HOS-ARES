"""出网工具（27 N1）：``send`` 只接受 ``RouteBinding``，不接受字符串。

B1.5 只冻结签名与类型守卫；真正的传输在 B10 接线（那时挂 guard 四处校验与令牌桶）。
"""

from __future__ import annotations

from typing import Mapping

from ..models import HttpTrace
from ..routes.types import RouteBinding


class TransportUnavailable(RuntimeError):
    """传输实现尚未接线（B10）。"""


def send(
    binding: RouteBinding,
    params: Mapping[str, str],
    auth: str | None = None,
) -> HttpTrace:
    """唯一签名。传字符串即 ``TypeError``——类型层不允许“拼出地址再发”（L0-04）。"""
    if not isinstance(binding, RouteBinding):
        raise TypeError(
            f"send() 只接受 RouteBinding，收到 {type(binding).__name__}；"
            "地址必须由 route_discovery 解析（27 N1）"
        )
    raise TransportUnavailable("HTTP 传输在 B10 接线；此批只冻结签名")
