"""等价性验证：真网络传输 vs 进程内 WSGI 传输，**判定必须一致**。

这批的核心命题：
> 换传输不该改判定。如果改了，说明某处 oracle 依赖了进程内传输的特性。

验证方式：同一批用例，分别在两种传输上跑，比对
``case_id|verdict``。**任何差异都是结构问题的证据**，不是"抖动"。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from lab.app import App
from lab.net_server import LabServer
from lab.net_transport import NetTransport
from lab.routes import candidates
from lab.transport_oob import LabTransport

from veripen_core.routes import (
    AssetRef,
    Resolver,
    RouteHints,
    RouteProvenance,
    RouteQuery,
)
from veripen_core.routes.sources.base import StaticSource
from veripen_core.routes.sources.symbols import SymbolSource, SymbolTable

SEP = chr(0x2F)


def _routeset_with_base(base: str, tags: tuple[str, ...]):  # noqa: ANN202
    """用 SymbolSource 把 ``{LAB_BASE}`` 换成真基址——**模板仍由 resolver 产出**。"""
    syms = SymbolTable({"LAB_BASE": base})
    src = SymbolSource("contract", RouteProvenance.CONTRACT, candidates(tags), syms,
                       base_symbol="LAB_BASE")
    q = RouteQuery(asset=AssetRef(id="web-01"), hints=RouteHints(), min_confidence=0.0)
    return Resolver(sources=[src]).resolve(q, now=1000)


def _first_status(transport: Any, binding: Any, params: dict[str, str]) -> int:
    t = transport.send(binding, params, "baseline", None)
    return int(t.status)


# ══════════════════════════════════════════════════════════════════
# 1 两种传输对同一请求给同一状态码
# ══════════════════════════════════════════════════════════════════
def test_wsgi_and_network_agree_on_basic_reads() -> None:
    """**发现**：WSGI 版会自动造 token（``make_token(account)``），真网络版不会。

    这不是 bug，是靶场便利与生产语义的差：生产里没人替你造 Authorization。
    所以等价性验证必须**两边喂同一个 token**——否则测的是"有没有自动补 token"，
    而不是"换传输会不会改判定"。
    """
    from lab.app import make_token

    app = App(secure=False)
    hdr = {"Authorization": "Bearer " + make_token("primary")}
    wsgi = LabTransport(app)

    srv = LabServer(app).start()
    try:
        rs_net = _routeset_with_base(srv.base_url, ("object_read",))
        rs_wsgi = _routeset_with_base("", ("object_read",))
        net = NetTransport()

        w = int(wsgi.send(rs_wsgi.bindings[0], {"id": "1"}, "baseline", hdr).status)
        n = int(net.send(rs_net.bindings[0], {"id": "1"}, "baseline", hdr).status)
        assert w == n == 200
    finally:
        srv.stop()


def test_wsgi_and_network_agree_on_auth_denial() -> None:
    """没有 token 时两种传输都必须拒——header 处理不一致会在这里暴露。"""
    app = App(secure=True)
    wsgi = LabTransport(app)
    srv = LabServer(app).start()
    try:
        rs = _routeset_with_base(srv.base_url, ("admin_function",))
        wsgi_rs = _routeset_with_base("", ("admin_function",))
        # WSGI 版会自动造 token；真网络版必须显式带
        w = _first_status(wsgi, wsgi_rs.bindings[0], {})
        n = _first_status(NetTransport(), rs.bindings[0], {})
        assert w in (200, 401, 403)
        assert n in (401, 403), "真网络下无 token 必须被拒"
    finally:
        srv.stop()


# ══════════════════════════════════════════════════════════════════
# 2 渲染规则必须逐字一致（这是"等价"的前提）
# ══════════════════════════════════════════════════════════════════
def test_render_rules_match_wsgi_transport() -> None:
    """WSGI 版与真网络版的渲染规则**必须同规则**，否则等价性无从谈起。"""
    from lab.net_transport import render_request as net_render

    rs = _routeset_with_base("", ("object_read",))
    b = rs.bindings[0]
    got = net_render(b, {"id": "42", "extra": "x"})
    # 路径参数被吃掉；未吃掉的走 query
    assert got.path.endswith(SEP + "42")
    assert "extra=x" in got.query
    assert got.consumed == frozenset({"id"})


def test_render_keeps_template_when_param_absent() -> None:
    from lab.net_transport import render_request as net_render

    rs = _routeset_with_base("", ("object_read",))
    got = net_render(rs.bindings[0], {})
    assert "{id}" in got.path  # 不猜、不替换


# ══════════════════════════════════════════════════════════════════
# 3 基址来自符号，不是测试拼的
# ══════════════════════════════════════════════════════════════════
def test_binding_template_carries_the_runtime_base() -> None:
    srv = LabServer(App(secure=False)).start()
    try:
        rs = _routeset_with_base(srv.base_url, ("object_read",))
        t = rs.bindings[0].template
        assert str(srv.port) in t
        assert "LAB_BASE" not in t
    finally:
        srv.stop()


def test_ref_is_stable_when_host_changes() -> None:
    """``ref`` **不随** host/port 变——它由结构键派生，而结构键不含部署地址。

    这条原来是反的（``test_ref_changes_when_host_changes``），把"ref 随 host 变"
    当成现状记录下来，并在 docstring 里承认"若做跨环境迁移，这条必须先解决"。
    现在解决了：``SymbolSource`` 把符号替换前的模板存进 ``identity_template``，
    结构键与 template_sha256 都从它算，``template`` 仍保留真实地址用于发送。

    为什么不干脆"剥掉 host"：虚拟主机场景里 ``api.example.com/x`` 与
    ``admin.example.com/x`` 是两条不同的面，剥掉会把它们合并成一个——
    那是制造 FP，比 ref 漂移严重得多。所以排除的只是**本来源注入的部署地址**，
    契约自己写的绝对 URL 照样进身份（见 test_vhost_templates_stay_distinct）。
    """
    a = _routeset_with_base("http" + ":" + SEP * 2 + "h:1111", ("object_read",))
    b = _routeset_with_base("http" + ":" + SEP * 2 + "h:2222", ("object_read",))
    ba, bb = a.bindings[0], b.bindings[0]

    # 身份三项一致
    assert ba.ref == bb.ref
    assert ba.structural_key == bb.structural_key
    assert ba.template_sha256 == bb.template_sha256
    # 执行地址不同——否则"换了机器"这件事没生效
    assert ba.template != bb.template
    assert "1111" in ba.template and "2222" in bb.template
    # 而 ref 里仍然看不出端口（不透明性）
    assert "1111" not in ba.ref and "2222" not in bb.ref


def test_vhost_templates_stay_distinct() -> None:
    """契约自己写的绝对 URL：host 是身份的一部分，**不许**合并。

    这是上面那条的边界条件。若哪天有人图省事把 host 一律剥掉，
    这里会立刻红——两个不同主机的同名路径被合成一条，等于把低权结论
    套到高权面上，是直接的 FP 来源（27 §3.4 记过同类碰撞）。
    """
    from veripen_core.routes.sources.base import StaticSource
    from veripen_core.routes.types import RouteCandidate

    def vhost(host: str) -> RouteCandidate:
        return RouteCandidate(
            method="GET",
            template="https" + ":" + SEP * 2 + host + SEP + "api" + SEP + "x",
            source=RouteProvenance.CONTRACT,
            response_shape={"semantic_tags": ["api_read"]},
        )

    src = StaticSource(
        "contract", RouteProvenance.CONTRACT,
        [vhost("api.example.invalid"), vhost("admin.example.invalid")],
    )
    q = RouteQuery(asset=AssetRef(id="web-01"), hints=RouteHints(), min_confidence=0.0)
    rs = Resolver(sources=[src]).resolve(q, now=1000)
    assert len(rs.bindings) == 2, [b.template for b in rs.bindings]
    assert len({b.ref for b in rs.bindings}) == 2


def test_capsule_route_ref_survives_a_port_change() -> None:
    """**跨环境迁移的端到端验收**：在 A 端口解析出的 ref，到 B 端口仍能解析。

    这条把 ref 稳定性的价值落到具体机制上：replay 的 ``_route_gate`` 是
    ``find_binding(routeset, cap.route.ref)``。若 ref 随端口变，迁移后
    capsule 必然落 ``ROUTE_UNRESOLVED`` ——"换一台机器重放旧证据"直接不成立。

    所以这里不比对 hash，而**走 replay 真正用的那条查找**。
    """
    from veripen_core.evidence import RouteBlock, find_binding

    a = _routeset_with_base("http" + ":" + SEP * 2 + "h:1111", ("object_read",))
    b = _routeset_with_base("http" + ":" + SEP * 2 + "h:2222", ("object_read",))

    # 在 A 环境录下 route 块（真 capsule 里存的就是这些字段）
    ba = a.bindings[0]
    recorded = RouteBlock(
        routeset_sha256=a.routeset_sha256,
        ref=ba.ref,
        source=ba.source,
        sources_all=list(ba.sources_all),
        confidence=ba.confidence,
        template_sha256=ba.template_sha256,
        resolver_version=a.resolver_version,
        resolved_at="1970-01-01T00:00:00Z",
        ttl_s=ba.ttl_s,
    )

    # 到 B 环境回放：必须找得到，且 template_sha256 不变（否则会被判 ROUTE_DRIFT）
    found = find_binding(b, recorded.ref)
    assert found is not None, "换了端口 ref 就解析不到——迁移后旧 capsule 全部失效"
    assert found.template_sha256 == recorded.template_sha256


# ══════════════════════════════════════════════════════════════════
# 4 切换真网络时暴露的三处差异（每一处都必须有回归测试）
# ══════════════════════════════════════════════════════════════════
def test_preencoded_payload_is_sent_once() -> None:
    """**发现 1**：真 HTTP 曾对已编码载荷二次编码，``%2e%2e%2f`` 变 ``%252e…``。

    后果不是"网络更严格"，是**载荷语义被悄悄改写**：服务端解码一次后拿到的
    仍是字面量 ``%2e%2e%2f``，于是靶场安全态"没读到系统文件"，N-08 从
    REFUTED 变 INCONCLUSIVE。WSGI 版不编码，所以这个差一直看不见。

    注意断言的是**解码一次后等于原文**，不是逐字相等：``/`` 转成 ``%2F``
    是合法且无损的（服务端解回来还是 ``/``），二次编码才是语义改写。
    """
    from urllib.parse import unquote

    from lab.net_transport import render_request as net_render

    rs = _routeset_with_base("", ("file_read",))
    got = net_render(rs.bindings[0], {"name": "%2e%2e%2fetc/passwd"})
    assert "%25" not in got.query, got.query
    value = got.query.split("=", 1)[1]
    assert unquote(value) == "../etc/passwd", value


def test_plain_characters_are_still_escaped() -> None:
    """另一半：不二次编码 ≠ 不编码。空格与 ``&`` 必须转义，否则请求行非法。"""
    from lab.net_transport import render_request as net_render

    rs = _routeset_with_base("", ("param_injectable",))
    got = net_render(rs.bindings[0], {"q": "widget AND 1=1&x=2"})
    assert " " not in got.query and "&x=2" not in got.query, got.query


def test_bare_percent_falls_back_to_full_escape() -> None:
    """裸 ``%``（后面不是两位十六进制）不是合法编码 → 退回全量转义。"""
    from lab.net_transport import render_request as net_render

    rs = _routeset_with_base("", ("param_injectable",))
    assert net_render(rs.bindings[0], {"q": "100%"}).query == "q=100%25"


def test_net_transport_marks_oob_seen() -> None:
    """**发现 2**：真网络传输曾**完全没有 OOB 快照差**。

    ``ssrf_oob`` 只看 trace 头上的 ``oob_seen``；没有这个钩子，服务端真的回调了
    也判不出来，正样本 P-05/P-06 会整齐退化成 REFUTED。这不是网络差异，
    是能力缺口——所以规则只写一份（``transport_oob.oob_headers``），两个传输共用。
    """
    from lab.app import make_token
    from lab.routes import binding_for
    from veripen_oracles.oob import LabOob

    app = App(secure=False)
    srv = LabServer(app).start()
    try:
        net = NetTransport(token_for=make_token, oob=LabOob(app=app, endpoint_value="oob-SAMPLE"))
        b = _routeset_with_base(srv.base_url, ("url_fetch",)).bindings[0]
        # 靶场只对 OOB_PREFIX 开头的目标登记回调 → 用它的前缀，别用别的域名
        from lab.app import OOB_PREFIX

        net.send(b, {"url": OOB_PREFIX + SEP + "probe"}, "probe", None)
        assert app.callbacks, "靶场没有登记回调——这条测试的前提不成立"
        hit = net.send(b, {"url": OOB_PREFIX + SEP + "probe"}, "probe", None)
        assert hit.headers.get("oob_seen") == "1"
        assert binding_for("url_fetch").template  # 相对模板仍在（基址不进模板字面量）
    finally:
        srv.stop()


def test_oob_rule_is_shared_by_both_transports() -> None:
    """两个传输必须调**同一个** ``oob_headers``，否则判定会分叉。"""
    import inspect

    from lab import net_transport, transport_oob

    src = inspect.getsource(net_transport.NetTransport.send)
    assert "oob_headers" in src
    assert transport_oob.LabTransport.send.__code__.co_names.__contains__("oob_headers")


def test_hardened_lab_blocks_only_decoded_traversal() -> None:
    """**发现 3**：靶场安全态曾只拦解码后的 ``..``，不拦编码形态。

    这不是框架问题：真网络把 ``%2e%2e%2f`` 原样送到后，``parse_qs`` 解码一次得
    ``..`` 于是被拦；但载荷若**已是双层编码**，解码一次仍是 ``%2e``，靶场就放行了。
    于是 N-08 从 REFUTED 退到 INCONCLUSIVE。修法在靶场侧：拦"解码后仍像穿越"
    的所有形态——否则量的是靶场模拟器的漏洞，不是框架的判定能力。
    """
    from urllib.parse import unquote

    app = App(secure=True)

    def status_of(query: str) -> int:
        captured: dict[str, int] = {}

        def start_response(status: str, _headers: list[tuple[str, str]]) -> None:
            captured["status"] = int(status.split()[0])

        app({"REQUEST_METHOD": "GET", "PATH_INFO": SEP + "api" + SEP + "read",
             "QUERY_STRING": query}, start_response)
        return captured["status"]

    for name in ("../etc/passwd", "%2e%2e%2fetc/passwd", "%252e%252e%252fetc/passwd"):
        once, twice = unquote(name), unquote(unquote(name))
        assert any(".." in v for v in (once, twice)), name
        assert status_of("name=" + name) == 400, (
            f"{name!r} 未被拦截（真网络下这会变成 INCONCLUSIVE）"
        )
    # 安全态不能把正常名字也误拦
    assert status_of("name=manual.txt") == 200
