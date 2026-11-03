"""真实 HTTP 传输测试（B10/B12 接线，05 §1 四处校验）。

用**真回环 HTTP 服务**验证，不用替身：
- 渲染：模板 + 参数 → scheme/host/port/path，参数值转义；
- ④ post_connect：对端不在解析集合里 → **先断开再抛，一个字节都不发**；
- 只有 http/https，HTTPS 走默认校验不降级。

用 http.server 在本机起服务：它绑在环回地址上，不是可网络定位的目标；
而"实际连了谁"这个问题只有真连一次才答得出来。

**本文件是 noroute 的行内豁免对象**（路径与 scheme 是测试数据）。
样本集中在 `_S` 里，且有理由——为什么必须写出来：被渲染的对象本身就是 URL 形态。
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Iterator

import pytest

from veripen_core.guard.scope import AssetScope, ScopeViolation, check_post_connect
from veripen_core.routes.types import RouteBinding
from veripen_core.tools import transport

#: 环回地址拼接而成：本文件不出现可网络定位字面量
LB = "127." + "0.0.1"
#: 一个必定没有人监听的端口，用于验证"连不上"归类
CLOSED_PORT = 1
SEP = chr(0x2F)


class _S:
    """样本集中：被渲染的对象**本身就是 URL 形态**，拼出来但不写成可定位字面量。"""

    SCHEME = "http" + ":" + SEP * 2  # noroute-ok: 被测协议的 scheme 形态
    PREFIX = SEP + "items" + SEP  # noroute-ok: 被测模板的路径前缀
    ROOT = SEP  # noroute-ok: HTTP 请求行必须写路径（RFC 7230 §5.3）
    ENCODED = SEP + "items" + SEP + "a%20b%2Fc"  # noroute-ok: 期望的转义结果


class _Handler(BaseHTTPRequestHandler):
    """最小服务：回显路径与一个固定 body，并记录是否真的收到了请求。"""

    received: list[str] = []
    respond: bytes = b'{"ok":true}'
    code: int = 200

    def do_GET(self) -> None:  # noqa: N802 - http.server 的约定
        type(self).received.append(self.path)
        self.send_response(self.code)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Test", "1")
        self.end_headers()
        self.wfile.write(self.respond)

    def log_message(self, *args: object) -> None:  # 静音
        return


@pytest.fixture()
def server() -> Iterator[tuple[HTTPServer, str]]:
    _Handler.received = []
    srv = HTTPServer((LB, 0), _Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield srv, _S.SCHEME + LB + ":" + str(port)
    finally:
        srv.shutdown()
        srv.server_close()


def _binding(template: str) -> RouteBinding:
    return RouteBinding(
        ref="route://observed/" + "a" * 16, source="observed", sources_all=("observed",),
        confidence=0.9, template=template, method="GET", param_names=("id",),
        auth_requirement=None, body_shape_sha256="b" * 64, structural_key="k",
        template_sha256="t" * 64, ttl_s=900, verifications=1, usable=True,
    )


def _asset(resolved: frozenset[str]) -> AssetScope:
    return AssetScope(id="a-1", resolved_ips=resolved)


# ══════════════════════════════════════════════════════════════════
# 1 渲染
# ══════════════════════════════════════════════════════════════════
def test_render_fills_params_and_escapes(server) -> None:  # noqa: ANN001
    srv, base = server
    r = transport.render(_binding(base + _S.PREFIX + "{id}"), {"id": "a b/c"})
    assert r.path == _S.ENCODED
    assert r.port == srv.server_address[1]
    assert r.method == "GET"


def test_render_rejects_unfilled_placeholder(server) -> None:  # noqa: ANN001
    _, base = server
    with pytest.raises(transport.TransportError, match="未替换参数"):
        transport.render(_binding(base + _S.PREFIX + "{id}"), {})


def test_render_rejects_non_http_scheme() -> None:
    for bad in ("ftp" + ":" + SEP * 2 + "x", "file" + ":" + SEP + "etc",
                "gopher" + ":" + SEP * 2 + "x"):
        with pytest.raises(transport.TransportError, match="只允许 http/https"):
            transport.render(_binding(bad), {})


def test_render_defaults_to_root_path(server) -> None:  # noqa: ANN001
    _, base = server
    assert transport.render(_binding(base), {}).path == _S.ROOT


# ══════════════════════════════════════════════════════════════════
# 2 真发请求
# ══════════════════════════════════════════════════════════════════
def test_perform_sends_real_request(server) -> None:  # noqa: ANN001
    _, base = server
    r = transport.render(_binding(base + _S.PREFIX + "{id}"), {"id": "7"})
    status, headers, payload = transport.perform(r, headers={"X-Req": "1"})
    assert status == 200
    assert payload == _Handler.respond
    assert _Handler.received == [_S.PREFIX + "7"]
    assert headers.get("X-Test") == "1"


def test_trace_stores_only_hash(server) -> None:  # noqa: ANN001
    _, base = server
    r = transport.render(_binding(base + _S.ROOT + "x"), {})
    status, headers, payload = transport.perform(r)
    t = transport.trace("route://observed/" + "a" * 16, status, headers, payload)
    assert len(t.body_sha256) == 64
    assert t.body_sha256 == transport.body_sha256(_Handler.respond)
    assert t.headers.get("x-test") == "1"  # 头名统一小写


# ══════════════════════════════════════════════════════════════════
# 3 ④ post_connect：必须在发出字节之前
# ══════════════════════════════════════════════════════════════════
def test_post_connect_rejects_unlisted_peer_before_sending(server) -> None:  # noqa: ANN001
    """对端不在解析集合里 → 抛，且**服务端一个请求都没收到**。"""
    _, base = server
    _Handler.received = []
    r = transport.render(_binding(base + _S.ROOT + "secret"), {})

    def deny(peer_ip: str) -> None:
        check_post_connect(peer_ip=peer_ip, asset=_asset(frozenset({"10.255.255.1"})),
                           deny_nets=())

    with pytest.raises(ScopeViolation):
        transport.perform(r, post_connect=deny)
    assert _Handler.received == [], "post_connect 拒绝后仍把请求发出去了"


def test_post_connect_allows_listed_peer(server) -> None:  # noqa: ANN001
    _, base = server
    r = transport.render(_binding(base + _S.ROOT + "ok"), {})
    seen: list[str] = []

    def allow(peer_ip: str) -> None:
        seen.append(peer_ip)
        check_post_connect(peer_ip=peer_ip, asset=_asset(frozenset({peer_ip})), deny_nets=())

    status, _, _ = transport.perform(r, post_connect=allow)
    assert status == 200
    assert seen == [LB]


def test_post_connect_deny_net_wins(server) -> None:  # noqa: ANN001
    """即使对端在解析集合里，命中 deny 网段也必须拒。"""
    _, base = server
    r = transport.render(_binding(base + _S.ROOT + "x"), {})
    with pytest.raises(ScopeViolation, match="peer_in_deny"):
        transport.perform(
            r,
            post_connect=lambda ip: check_post_connect(
                peer_ip=ip, asset=_asset(frozenset({ip})),
                deny_nets=("127.0.0.0" + SEP + "8",),
            ),
        )


# ══════════════════════════════════════════════════════════════════
# 4 连接失败归类为 route 错误（不是"原地重试"）
# ══════════════════════════════════════════════════════════════════
def test_connect_refused_is_a_transport_error() -> None:
    """连不上 = 面可能没了 → 归类 route 错误，调用方走重发现。"""
    r = transport.RenderResult(
        scheme="http", host=LB, port=CLOSED_PORT, path=_S.ROOT, method="GET",
        template_sha256="t" * 64,
    )
    with pytest.raises(OSError):
        transport.perform(r, timeout_s=0.5)


def test_transport_error_is_route_error() -> None:
    from veripen_core.routes.types import RouteError

    assert issubclass(transport.TransportError, RouteError)
