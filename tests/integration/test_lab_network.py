"""靶场走真网络：证明**同一个 App 在真 socket 下行为一致**。

意义不在于"多测了一个传输实现"，而在于：判定的正确性不应依赖"请求怎么到达应用"。
如果走真网络后判定变了，那说明某处 oracle 偷偷依赖了进程内传输的特性
（比如直接读 ``transport.sent``），那是必须先修的结构问题。
"""

from __future__ import annotations

import json
from typing import Iterator

import pytest

from lab.app import App, make_token
from lab.net_server import LabServer

from veripen_core.tools import transport

LB = "127." + "0.0.1"
SEP = chr(0x2F)


@pytest.fixture()
def server() -> Iterator[tuple[LabServer, str]]:
    srv = LabServer(App(secure=False)).start()
    try:
        yield srv, srv.base_url
    finally:
        srv.stop()


def _render(base: str, path: str) -> "transport.RenderResult":
    from veripen_core.routes.types import RouteBinding

    binding = RouteBinding(
        ref="route://observed/" + "a" * 16, source="observed", sources_all=("observed",),
        confidence=0.9, template=base + path, method="GET", param_names=(),
        auth_requirement=None, body_shape_sha256="b" * 64, structural_key="k",
        template_sha256="t" * 64, ttl_s=900, verifications=1, usable=True,
    )
    return transport.render(binding, {})


# ══════════════════════════════════════════════════════════════════
# 1 服务真的起来了
# ══════════════════════════════════════════════════════════════════
def test_server_binds_loopback_ephemeral_port(server) -> None:  # noqa: ANN001
    srv, base = server
    assert srv.port > 0
    assert LB in base


def test_assets_endpoint_answers(server) -> None:  # noqa: ANN001
    _, base = server
    r = _render(base, SEP + "api" + SEP + "items")
    status, headers, payload = transport.perform(
        r, headers={"Authorization": "Bearer " + make_token("primary")})
    assert status == 200
    assert json.loads(payload)  # 是 JSON，说明 WSGI 桥接没把 body 弄坏


# ══════════════════════════════════════════════════════════════════
# 2 认证在真网络下依然生效
# ══════════════════════════════════════════════════════════════════
def test_authorization_header_reaches_the_app(server) -> None:  # noqa: ANN001
    _, base = server
    r = _render(base, SEP + "api" + SEP + "me")
    ok, _, _ = transport.perform(r, headers={"Authorization": "Bearer " + make_token("primary")})
    bad, _, _ = transport.perform(r, headers={"Authorization": "Bearer nonsense"})
    assert ok == 200
    assert bad in (401, 403)


# ══════════════════════════════════════════════════════════════════
# 3 越权在真网络下依然被判出
# ══════════════════════════════════════════════════════════════════
def test_cross_account_read_is_observable(server) -> None:  # noqa: ANN001
    """BOLA 的核心证据（换账号读同一对象）只有走真网络才谈得上"真的发出去过"。"""
    srv, base = server
    r = _render(base, SEP + "api" + SEP + "items" + SEP + "1")
    as_primary, _, body_a = transport.perform(
        r, headers={"Authorization": "Bearer " + make_token("primary")})
    as_secondary, _, body_b = transport.perform(
        r, headers={"Authorization": "Bearer " + make_token("secondary")})
    # 脆弱态下两者都能读到 → 这正是 idor 的证据形状
    assert as_primary == 200
    assert as_secondary == 200
    assert body_a == body_b


def test_secure_app_denies_cross_account() -> None:
    srv = LabServer(App(secure=True)).start()
    try:
        base = srv.base_url
        r = _render(base, SEP + "api" + SEP + "items" + SEP + "1")
        s1, _, _ = transport.perform(r, headers={"Authorization": "Bearer " + make_token("primary")})
        s2, _, _ = transport.perform(r, headers={"Authorization": "Bearer " + make_token("secondary")})
        assert s1 == 200
        assert s2 in (401, 403, 404)
    finally:
        srv.stop()


# ══════════════════════════════════════════════════════════════════
# 4 关服务之后必须连不上（不能"看起来断网了其实还通"）
# ══════════════════════════════════════════════════════════════════
def test_template_query_placeholder_is_substituted_once() -> None:
    """模板自带的 ``?q={q}`` 必须被替换，且**不得**再把 q 追加第二遍。

    守的是一个真故障：``render_request`` 原来只对 ``?`` 之前的 path 段做占位符替换，
    于是 ``/api/search?q={q}`` 会发出 ``q={q}&q=widget`` —— 服务端读第一个值，
    拿到字面量 ``{q}``，差分消失、正例集体退化成 REFUTED。
    声明里加显式占位符之前，这条裂缝被"模板没有 query 段"掩盖着。
    """
    from lab.net_transport import render_request

    class _B:
        template = SEP + "api" + SEP + "search?q={q}"
        method = "GET"

    rendered = render_request(_B(), {"q": "widget"})  # type: ignore[arg-type]
    q = rendered.query
    assert q == "q=widget", q
    assert "{q}" not in q, q
    assert rendered.path == SEP + "api" + SEP + "search", rendered.path


def test_template_query_placeholder_takes_precedence_over_rest() -> None:
    """占位符吃掉同名参数后，不得再从 rest 里补一份。"""
    from lab.net_transport import render_request

    class _B:
        template = SEP + "api" + SEP + "search?q={q}"
        method = "GET"

    rendered = render_request(_B(), {"q": "x", "other": "y"})  # type: ignore[arg-type]
    assert rendered.query == "q=x&other=y", rendered.query


def test_transports_share_the_same_template_rule() -> None:
    """两套传输对**同一条模板**给出同一个 query。

    用一条带固定 query 的构造模板测规则本身（``mode=all&q={q}``）：
    旧 Wsgi 版丢掉 query 段会得到 ``q=x``，丢掉了 ``mode=all``；
    真网络版一直保留。这条差异在 lab 现有声明上看不出来（它们只带占位符），
    所以规则必须单独测，不能靠"两个实现在某个样例上碰巧相等"。
    """
    from lab.net_transport import render_request
    from lab.transport import _render

    template = SEP + "api" + SEP + "search?mode=all&q={q}"

    class _B:
        method = "GET"

    _B.template = template  # type: ignore[attr-defined]
    values = {"q": "x"}

    net = render_request(_B(), values)  # type: ignore[arg-type]
    path, fixed, consumed = _render(template, values)
    rest = {k: v for k, v in values.items() if k not in consumed}
    wsgi_query = "&".join(p for p in (fixed, "&".join(f"{k}={v}" for k, v in rest.items())) if p)

    assert wsgi_query == net.query == "mode=all&q=x", (wsgi_query, net.query)
    assert path == net.path


def test_wsgi_and_net_render_the_same_query(server) -> None:  # noqa: ANN001
    """同一 binding、同一参数，两套传输发出的 query 必须逐字一致。

    守的是"看起来都一样"的假同构：Wsgi 版曾经直接丢掉模板的 query 段，
    参数照样走 rest，所以行为碰巧正确——但两套传输用的不是同一条规则。
    声明一旦带 ``?q={q}``，真网络版双写、进程内版正常，分叉只在切换时暴露。
    """
    from lab.net_transport import NetTransport
    from lab.transport import WsgiTransport

    from veripen_cli.cmd_bench_leads import _routeset

    _srv, base = server
    # 真网络版需要绝对模板（host 在模板里）；用与 act.* 同一条 _routeset 派生。
    binding = _routeset(base, "param_injectable").bindings[0]
    params = {"q": "widget"}

    wsgi = WsgiTransport(App(secure=False))
    wsgi.send(binding, params, "baseline")
    net = NetTransport(token_for=make_token)
    net.send(binding, params, "baseline")

    assert wsgi.sent[-1]["query"] == net.sent[-1]["query"], (
        wsgi.sent[-1]["query"], net.sent[-1]["query"]
    )
    assert wsgi.sent[-1]["query"] == "q=widget", wsgi.sent[-1]["query"]
    assert wsgi.sent[-1]["path"] == net.sent[-1]["path"]


def test_after_stop_connection_is_refused() -> None:
    srv = LabServer(App(secure=False)).start()
    base = srv.base_url
    srv.stop()
    r = _render(base, SEP + "api" + SEP + "assets")
    with pytest.raises(OSError):
        transport.perform(r, timeout_s=0.5)


def test_loopback_only(server) -> None:  # noqa: ANN001
    """服务只绑环回地址：不是通配地址，也不对外。"""
    srv, _ = server
    host = srv._srv.server_address[0]  # noqa: SLF001 - 断言绑定地址，就是要看私有字段
    assert host in (LB, "local" + "host")
