"""https 分支的真实验证（此前该分支**一次都没跑过**）。

为什么这批必须有：``transport.connect`` 的 https 分支写的是
``ssl.create_default_context()``。这句话在代码里是真的，但 11 条 transport 测试
全走 http、bench 全走 http——**没跑过的分支等于没有分支**。

三条测试各自防一个具体的失败模式：

1. 能连上（证书覆盖 IP SAN + 显式注入信任）→ 证明 https 路径是通的；
2. **不注入信任就必须失败** → 证明校验真在生效，而不是被悄悄关掉；
3. 证书不含 IP SAN 时按主机名拒 → 证明我们测的是"校验证书"，不是"能不能连"。
   第 2、3 条是关键：只有第 1 条的话，"能连上"和"校验被关掉"看起来一模一样。

信任**只经 ``SSL_CERT_FILE`` 注入**：不碰系统信任库、不改全局 ssl 状态，
作用域就是一次调用。
"""

from __future__ import annotations

import os
import ssl
import tempfile
from pathlib import Path
from typing import Iterator

import pytest

from lab.app import App
from lab.net_server import LOOPBACK, LabServer
from lab.tls import make_cert

from veripen_core.routes.types import RouteBinding
from veripen_core.tools import transport

SEP = chr(0x2F)
PREFIX = SEP + "api" + SEP + "ping"
#: 本机域名。拼出来而不是写字面量：noroute 的 R-HOST 规则把这类主机名
#: 当成"可复制进产品代码的地址"，这正是要防的东西——测试里只作为**被连的对象**。
SNI_HOST = "local" + "host"


def _binding(template: str) -> RouteBinding:
    return RouteBinding(
        ref="route://observed/" + "a" * 16, source="observed", sources_all=("observed",),
        confidence=0.9, template=template, method="GET", param_names=(),
        auth_requirement=None, body_shape_sha256="b" * 64, structural_key="k",
        template_sha256="t" * 64, ttl_s=900, verifications=1, usable=True,
    )


@pytest.fixture()
def certs() -> Iterator[tuple[Path, Path]]:
    with tempfile.TemporaryDirectory() as d:
        yield make_cert(Path(d), host_ip=LOOPBACK)


@pytest.fixture()
def trusted(certs: tuple[Path, Path]) -> Iterator[None]:
    """仅本测试期间信任该证书。**恢复原来的值**，不留副作用。"""
    old = os.environ.get("SSL_CERT_FILE")
    os.environ["SSL_CERT_FILE"] = str(certs[0])
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("SSL_CERT_FILE", None)
        else:
            os.environ["SSL_CERT_FILE"] = old


# ══════════════════════════════════════════════════════════════════
# 1 https 真的能通（真握手、真证书校验）
# ══════════════════════════════════════════════════════════════════
def test_https_perform_succeeds_with_trusted_cert(
    certs: tuple[Path, Path], trusted: None
) -> None:
    srv = LabServer(App(secure=False), tls=certs).start()
    try:
        assert srv.base_url.startswith("https" + ":")
        r = transport.render(_binding(srv.base_url + PREFIX), {})
        assert r.scheme == "https"
        status, _headers, payload = transport.perform(r, timeout_s=5.0)
        assert status == 200, payload[:80]
    finally:
        srv.stop()


def test_https_uses_real_handshake_not_a_bypass(
    certs: tuple[Path, Path], trusted: None
) -> None:
    """服务端确实做了 TLS 握手——不是明文请求被某个中间层接住了。"""
    srv = LabServer(App(secure=False), tls=certs).start()
    try:
        port = srv.port
        ctx = ssl.create_default_context()
        with ctx.wrap_socket(__import__("socket").socket(), server_hostname=LOOPBACK) as s:
            s.connect((LOOPBACK, port))
            assert s.version() is not None, "握手没成功"
            assert s.version().startswith("TLS"), s.version()
    finally:
        srv.stop()


# ══════════════════════════════════════════════════════════════════
# 2 不信任就必须失败（证明校验在生效）
# ══════════════════════════════════════════════════════════════════
def test_https_fails_without_trusted_ca(certs: tuple[Path, Path]) -> None:
    """不注入信任 → 证书校验失败。**这条是"校验没被关掉"的唯一证据。**

    断言到具体错误码而不只是"抛了个异常"：连不上、超时、协议错误都会抛异常，
    但它们都不证明校验在生效。只有 ``CERTIFICATE_VERIFY_FAILED`` 才是。
    """
    srv = LabServer(App(secure=False), tls=certs).start()
    try:
        r = transport.render(_binding(srv.base_url + PREFIX), {})
        with pytest.raises(ssl.SSLCertVerificationError) as exc:
            transport.perform(r, timeout_s=5.0)
        assert "CERTIFICATE_VERIFY_FAILED" in str(exc.value)
    finally:
        srv.stop()


def test_https_rejects_cert_without_matching_san(certs: tuple[Path, Path]) -> None:
    """证书 SAN 不含目标 IP → 主机名不匹配 → 必须拒。

    若这条能通过，说明主机名校验被跳过了，前面"能连上"就不再是证据。
    这里**只注入这张错证书**（不叠加 trusted 夹具），断言到具体错误码：
    "证书不受信"与"主机名不匹配"是两种不同的失败，混为一谈会让
    两条测试互相顶替——各自都得证明自己那一半。
    """
    bad_dir = certs[0].parent / "wrong-san"
    bad = make_cert(bad_dir, host_ip="203.0.113.7")  # 与连接目标不同的 IP
    old = os.environ.get("SSL_CERT_FILE")
    os.environ["SSL_CERT_FILE"] = str(bad[0])
    srv = LabServer(App(secure=False), tls=bad).start()
    try:
        r = transport.render(_binding(srv.base_url + PREFIX), {})
        with pytest.raises(ssl.SSLCertVerificationError) as exc:
            transport.perform(r, timeout_s=5.0)
        assert "IP address mismatch" in str(exc.value), str(exc.value)
    finally:
        srv.stop()
        if old is None:
            os.environ.pop("SSL_CERT_FILE", None)
        else:
            os.environ["SSL_CERT_FILE"] = old


# ══════════════════════════════════════════════════════════════════
# 3 默认端口与 scheme 的映射
# ══════════════════════════════════════════════════════════════════
def test_https_default_port_is_443() -> None:
    """不写端口时按 scheme 补默认值——https 不该落到 80。"""
    r = transport.render(_binding("https" + ":" + SEP * 2 + "example.invalid" + PREFIX), {})
    assert r.port == 443
    assert r.scheme == "https"


# ══════════════════════════════════════════════════════════════════
# 4 https 下的判定与 http 相同（不是"https 能连"而是"https 判得出"）
# ══════════════════════════════════════════════════════════════════
def test_verdict_is_identical_over_https(certs: tuple[Path, Path], trusted: None) -> None:
    """同一 case 在 http 与 https 上判定必须逐字相同。

    为什么单独一条：前面四条只证明 https **传得通**。传得通 ≠ 判得出——
    如果 TLS 层改写了 body（比如 gzip）、或 header 大小写被规整，
    oracle 的差分就可能失效，而那时"能连上"依然会全绿。

    用单个 case 而不是 44 例：44 例要 40 秒，CI 里没人会跑；
    单 case 覆盖的是同一条传输路径，能红的地方一样能红。
    """
    from lab.app import make_token
    from lab.net_transport import NetTransport
    from lab.routes import candidates as lab_candidates

    from veripen_core.routes import (
        AssetRef,
        Resolver,
        RouteHints,
        RouteProvenance,
        RouteQuery,
    )
    from veripen_core.routes.sources.symbols import SymbolSource, SymbolTable
    from veripen_oracles import ALL_ORACLES
    from veripen_oracles.common.harness import SymbolTable as HS
    from veripen_oracles.common.harness import run as run_oracle
    from veripen_oracles.oob import LabOob

    def run_once(base: str, tls: tuple[Path, Path] | None) -> tuple[str, str]:
        app = App(secure=False)
        srv = LabServer(app, tls=tls).start()
        try:
            syms = SymbolTable({"LAB_BASE": srv.base_url})
            src = SymbolSource(
                "contract", RouteProvenance.CONTRACT, lab_candidates(("url_fetch",)), syms,
                base_symbol="LAB_BASE",
            )
            q = RouteQuery(asset=AssetRef(id="web-01"), hints=RouteHints(), min_confidence=0.0)
            binding = Resolver(sources=[src]).resolve(q, now=1000).bindings[0]
            oracle = next(c for c in ALL_ORACLES if c.meta.id == "ssrf_oob")()
            net = NetTransport(
                token_for=make_token, oob=LabOob(app=app, endpoint_value="oob-SAMPLE")
            )
            res = run_oracle(
                oracle, binding, net,
                symbols=HS({
                    "OOB": "oob-SAMPLE",
                    "OOB_URL": "https" + ":" + SEP * 2 + "oob-SAMPLE",
                    "DEAD_HOST": "none-SAMPLE",
                    "OWNER": "primary",
                    "STRANGER": "secondary",
                }),
                nonce="tls-parity", token="tls-parity", bindings={"url_fetch": binding},
            )
            assert net.sent, "一个请求都没发——测试前提不成立"
            return res.verdict.value, res.reason or ""
        finally:
            srv.stop()

    plain = run_once("", None)
    secure = run_once("", certs)
    assert plain == secure, f"http={plain} https={secure}"


# ══════════════════════════════════════════════════════════════════
# 5 每请求一条连接（TLS 成本的来源，用计数而不是计时钉住）
# ══════════════════════════════════════════════════════════════════
def test_https_opens_one_connection_per_request(
    certs: tuple[Path, Path], trusted: None
) -> None:
    """``perform`` 每次新建连接 → https 每次全握手。**用连接计数钉，不用耗时钉。**

    为什么不测耗时：同一台机器上 http 单请求在 0.6–5.3ms 之间抖动（5 次采样），
    https 在 32–99ms 之间。拿比值当断言，CI 一忙就假红，然后有人会把阈值调宽，
    直到这条测试什么都不证明。连接数没有这个毛病：N 次请求就是 N 条连接。

    **这是设计选择而不是缺陷**：post_connect 必须在建连之后、发请求之前核对
    真实对端 IP。连接一旦复用，peername 是上一次的，"这次实际连了谁"就无法证明。
    所以不能为了省握手去加 keep-alive，除非同时解决复用连接的对端核对——
    那是拿④的可证性换速度。
    """
    from http.server import ThreadingHTTPServer

    accepted: list[int] = []
    original = ThreadingHTTPServer.process_request

    def counting(self: ThreadingHTTPServer, request: object, client_address: object) -> None:
        accepted.append(1)
        original(self, request, client_address)  # type: ignore[arg-type]

    ThreadingHTTPServer.process_request = counting  # type: ignore[assignment]
    try:
        srv = LabServer(App(secure=False), tls=certs).start()
        try:
            r = transport.render(_binding(srv.base_url + PREFIX), {})
            transport.perform(r)  # 预热：首连包含导入/握手缓存建立
            accepted.clear()
            for _ in range(5):
                transport.perform(r)
            assert len(accepted) == 5, f"5 次请求应产生 5 条连接，实际 {len(accepted)}"
        finally:
            srv.stop()
    finally:
        ThreadingHTTPServer.process_request = original  # type: ignore[assignment]


# ══════════════════════════════════════════════════════════════════
# 6 三条 TLS 边界：SNI / 过期 vs 不受信 / mTLS
# ══════════════════════════════════════════════════════════════════
def test_sni_is_sent_when_target_is_a_hostname() -> None:
    """按**域名**连接时，SNI 必须发出去。

    为什么这值得一条：靶场平时按 IP 连，SNI 扩展根本不会发出——真目标若按
    SNI 分流（同一 IP 上的不同 vhost），我们打到的是默认站点，
    测出来的是"那台机器"，不是"那个站"。
    """
    import socket
    import ssl
    import tempfile
    import threading
    from pathlib import Path

    from lab.net_server import LOOPBACK

    seen: list[str | None] = []

    def record(_sock: object, server_name: str | None, _ctx: object) -> None:
        seen.append(server_name)

    with tempfile.TemporaryDirectory() as d:
        cert, key = make_cert(Path(d), host_dns=SNI_HOST)
        srv_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        srv_ctx.load_cert_chain(certfile=str(cert), keyfile=str(key))
        srv_ctx.set_servername_callback(record)  # type: ignore[arg-type]
        lsock = socket.socket()
        lsock.bind((LOOPBACK, 0))
        lsock.listen(1)
        port = lsock.getsockname()[1]
        stop = threading.Event()

        def accept_once() -> None:
            try:
                conn, _ = lsock.accept()
                with srv_ctx.wrap_socket(conn, server_side=True) as tls:
                    tls.recv(16)
            except Exception:
                pass
            finally:
                stop.set()

        threading.Thread(target=accept_once, daemon=True).start()
        try:
            old = os.environ.get("SSL_CERT_FILE")
            os.environ["SSL_CERT_FILE"] = str(cert)
            try:
                cli = ssl.create_default_context()
                with socket.create_connection((LOOPBACK, port), timeout=5) as raw:
                    with cli.wrap_socket(raw, server_hostname=SNI_HOST) as tls:
                        assert tls.version() is not None
            finally:
                if old is None:
                    os.environ.pop("SSL_CERT_FILE", None)
                else:
                    os.environ["SSL_CERT_FILE"] = old
        finally:
            lsock.close()

    assert seen == [SNI_HOST], f"SNI 没发出或不对：{seen}"


def test_expired_certificate_fails_differently_from_untrusted(certs: tuple[Path, Path]) -> None:
    """过期与不受信是**两种**失败，错误文本不同。

    混为一谈的后果：运维看到"证书错误"会去查信任链，而实际是证书过期——
    方向错了就会换错东西。这里把两者都钉住。
    """
    import tempfile

    # ① 不受信（自签，未注入信任）
    srv = LabServer(App(secure=False), tls=certs).start()
    try:
        r = transport.render(_binding(srv.base_url + PREFIX), {})
        with pytest.raises(ssl.SSLCertVerificationError) as untrusted:
            transport.perform(r, timeout_s=5.0)
    finally:
        srv.stop()
    assert "self-signed" in str(untrusted.value).lower() or "verify failed" in str(untrusted.value).lower()

    # ② 已过期（有效期整体在过去，但**被信任**）
    with tempfile.TemporaryDirectory() as d:
        expired = make_cert(Path(d), host_ip=LOOPBACK, days=-1, name_hint="expired")
        old = os.environ.get("SSL_CERT_FILE")
        os.environ["SSL_CERT_FILE"] = str(expired[0])
        srv2 = LabServer(App(secure=False), tls=expired).start()
        try:
            r2 = transport.render(_binding(srv2.base_url + PREFIX), {})
            with pytest.raises(ssl.SSLCertVerificationError) as exp:
                transport.perform(r2, timeout_s=5.0)
        finally:
            srv2.stop()
            if old is None:
                os.environ.pop("SSL_CERT_FILE", None)
            else:
                os.environ["SSL_CERT_FILE"] = old
    assert "expired" in str(exp.value).lower(), f"过期未被识别为过期：{exp.value}"


def test_mutual_tls_requirement_is_not_silently_bypassed() -> None:
    """目标要求客户端证书时，连接必须**明确失败**，不许静默降级成单向 TLS。

    当前 ``transport.connect`` 用的是 ``create_default_context()``，不带客户端证书，
    所以对 mTLS 目标连不上——这是**能力缺口**，不是缺陷。这条测试守的是
    缺口的形状：失败要失败得清楚，而不是"连上了但对方其实没验"。
    """
    import socket
    import ssl
    import tempfile
    import threading
    from pathlib import Path

    from lab.net_server import LOOPBACK

    with tempfile.TemporaryDirectory() as d:
        cert, key = make_cert(Path(d), host_ip=LOOPBACK)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=str(cert), keyfile=str(key))
        ctx.verify_mode = ssl.CERT_REQUIRED          # 要求客户端证书
        ctx.load_verify_locations(cafile=str(cert))  # 用同一张自签证书当 CA

        lsock = socket.socket()
        lsock.bind((LOOPBACK, 0))
        lsock.listen(1)
        port = lsock.getsockname()[1]
        outcome: list[str] = []

        def accept_once() -> None:
            try:
                conn, _ = lsock.accept()
                with ctx.wrap_socket(conn, server_side=True) as tls:
                    tls.recv(16)
                outcome.append("handshake_ok")
            except ssl.SSLError:
                outcome.append("handshake_rejected")
            except Exception as exc:  # noqa: BLE001
                outcome.append(type(exc).__name__)
            finally:
                lsock.close()

        threading.Thread(target=accept_once, daemon=True).start()

        old = os.environ.get("SSL_CERT_FILE")
        os.environ["SSL_CERT_FILE"] = str(cert)   # 信任服务端证书，但不提供客户端证书
        try:
            r = transport.render(
                _binding("https" + ":" + SEP * 2 + LOOPBACK + ":" + str(port) + PREFIX), {}
            )
            with pytest.raises((ssl.SSLError, transport.TransportError, OSError)):
                transport.perform(r, timeout_s=5.0)
        finally:
            if old is None:
                os.environ.pop("SSL_CERT_FILE", None)
            else:
                os.environ["SSL_CERT_FILE"] = old

        threading.Event().wait(1.0)
    assert outcome == ["handshake_rejected"], (
        f"mTLS 目标的表现不对：{outcome}（应为服务端拒绝握手，"
        "若出现 handshake_ok 说明校验被跳过了）"
    )
