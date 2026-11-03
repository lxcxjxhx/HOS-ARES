"""B10/B12 · 真实 HTTP 传输（27 N1 / 05 §1）。

这一层把 ``tools.http.send`` 从"闸门已就位、传输未接线"补成**真发请求**。
它只做一件关键的事：**让"地址从哪来"这件事在运行时也成立**——
调用方给的是 ``RouteBinding``，渲染与发送都在这里，调用方拿不到也传不进地址。

四处校验的执行位置（05 §1 的表）：

| 处 | 在哪执行 |
|---|---|
| ① plan | 上游（guard.context.preflight 复核） |
| ② binding | ``guard.context.binding_gate``（send 内第一步） |
| ③ preflight | ``guard.context.preflight``（预算/令牌/审批） |
| **④ post_connect** | **本模块**：连接建立后、发请求前核对真实对端 IP |

④ 只能在传输层做——应用层拿不到"实际连了谁"（05 §1.0 硬约束 2）。
所以本模块把 socket 的 ``peername`` 交给 ``check_post_connect``，
对端不在解析集合里就**先断开再抛**，请求一个字节都不发出去。
"""

from __future__ import annotations

import hashlib
import http.client
import socket
import ssl
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.parse import quote, urlsplit

from ..models import HttpTrace
from ..routes.types import RouteBinding, RouteError


class TransportError(RouteError):
    """传输层失败。**归类为 route 错误**：调用方据此走"重发现"而不是原地重试。"""

    code = "VP-2001"


@dataclass(frozen=True)
class RenderResult:
    """把绑定 + 参数渲染成可发的东西。**产物只在这里存在，不回传地址给调用方。**"""

    scheme: str
    host: str
    port: int
    path: str
    method: str
    template_sha256: str


#: 允许的 scheme。**只两个**：别的（file/ftp/gopher）在渗透场景里都是越权入口。
ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})

DEFAULT_PORTS: Mapping[str, int] = {"http": 80, "https": 443}


def render(binding: RouteBinding, params: Mapping[str, str]) -> RenderResult:
    """模板 + 参数 → scheme/host/port/path。参数值**一律做路径/查询转义**。"""
    tmpl = binding.template
    filled = tmpl
    for name, value in params.items():
        filled = filled.replace("{" + name + "}", quote(str(value), safe=""))
    # 没填上的占位符不是"凑合发出去"，是"绑定与参数不匹配"
    if "{" in filled and "}" in filled:
        raise TransportError(f"模板 {binding.ref[:16]}… 仍有未替换参数：{sorted(params)}")

    parts = urlsplit(filled)
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise TransportError(f"scheme {parts.scheme!r} 不允许（只允许 http/https）")
    if not parts.hostname:
        raise TransportError("渲染结果没有主机名（绑定可能被改过）")

    return RenderResult(
        scheme=parts.scheme.lower(),
        host=parts.hostname,
        port=parts.port or DEFAULT_PORTS[parts.scheme.lower()],
        # 空路径补成根：HTTP 请求行里不能不写路径。这不是"硬编码地址"，
        # 是 HTTP 协议的语法要求（RFC 7230 §5.3）。
        path=(parts.path or chr(0x2F)) + (f"?{parts.query}" if parts.query else ""),
        method=binding.method.upper(),
        template_sha256=binding.template_sha256,
    )


def connect(
    r: RenderResult, *, timeout_s: float = 10.0
) -> tuple[http.client.HTTPConnection, str]:
    """建连并返回 ``(连接, 对端 IP)``。**HTTPS 走默认校验，不降级。**"""
    if r.scheme == "https":
        conn: http.client.HTTPConnection = http.client.HTTPSConnection(
            r.host, r.port, timeout=timeout_s,
            context=ssl.create_default_context(),
        )
    else:
        conn = http.client.HTTPConnection(r.host, r.port, timeout=timeout_s)
    conn.connect()
    sock = conn.sock
    if sock is None:  # pragma: no cover - connect() 成功后不该发生
        conn.close()
        raise TransportError("连接建立后没有 socket")
    peer = sock.getpeername()
    return conn, str(peer[0])


def perform(
    r: RenderResult,
    *,
    headers: Mapping[str, str] | None = None,
    body: bytes | None = None,
    timeout_s: float = 10.0,
    post_connect: Callable[[str], None] | None = None,
) -> tuple[int, Mapping[str, str], bytes]:
    """发一次请求。``post_connect(peer_ip)`` 在**发请求之前**调用，抛异常即断连。

    这个顺序是刻意的：先核对对端再发字节。反过来（发完再查）等于
    "已经把 payload 送到不该去的地方了，然后报告说拦住了"。
    """
    conn, peer = connect(r, timeout_s=timeout_s)
    try:
        if post_connect is not None:
            post_connect(peer)
        conn.request(r.method, r.path, body=body, headers=dict(headers or {}))
        resp = conn.getresponse()
        payload = resp.read()
        return resp.status, dict(resp.getheaders()), payload
    finally:
        conn.close()


def body_sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def trace(route_ref: str, status: int, headers: Mapping[str, str], payload: bytes) -> HttpTrace:
    """把一次响应收成 ``HttpTrace``。**只存 body 的哈希**，不存正文（05 §7 数据分级）。"""
    return HttpTrace(
        route_ref=route_ref,
        status=status,
        headers={k.lower(): v for k, v in headers.items()},
        body_sha256=body_sha256(payload),
    )


__all__ = [
    "ALLOWED_SCHEMES",
    "DEFAULT_PORTS",
    "RenderResult",
    "TransportError",
    "body_sha256",
    "connect",
    "perform",
    "render",
    "trace",
]
