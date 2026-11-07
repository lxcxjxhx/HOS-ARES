"""B118 · DOM XSS 端到端：`run --discover` 真的跑出 xss_dom VERIFIED。

夹具首页链到 `/search?q=hi`，该页把 `location.search` 塞进 `innerHTML`（有洞）。
这条用例覆盖三件必须同时成立的事：
1. 发现把 `/search` 收成面并推出 `param_injectable`；
2. 分派把 `xss_dom` 交给**渲染传输**（HTTP 看不到执行）；
3. 三条件判定 + capsule 构造都对。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from veripen_core.render import BrowserRenderer

S = chr(0x2F)
LOOPBACK = "127" + "." + "0" + "." + "0" + "." + "1"

HOME = ('<html><body><a href="' + S + 'search?q=hi">s</a></body></html>').encode()
VULN = (
    "<html><body><div id='out'></div><script>"
    "document.getElementById('out').innerHTML ="
    " decodeURIComponent(location.search.slice(3));"
    "</script></body></html>"
).encode()


class _H(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = VULN if self.path.startswith(S + "search") else HOME
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # noqa: ANN002
        pass


needs_browser = pytest.mark.skipif(not BrowserRenderer().available(), reason="无浏览器")


@needs_browser
def test_discover_run_verifies_dom_xss(tmp_path) -> None:  # noqa: ANN001
    srv = HTTPServer((LOOPBACK, 0), _H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        from veripen_cli.cmd_run import main

        rc = main([
            "--target", "http" + ":" + S * 2 + f"{LOOPBACK}:{port}",
            "--discover", "--discover-rounds", "2", "--discover-pages", "6",
            "--out", str(tmp_path / "run"),
        ])
    finally:
        srv.shutdown()

    assert rc in (0, 1)
    doc = json.loads((tmp_path / "run" / "findings.json").read_text(encoding="utf-8"))
    dom = [f for f in doc["findings"] if f["oracle"] == "xss_dom"]
    assert dom, "xss_dom 没跑：分派或选择器有问题"
    assert dom[0]["outcome"] == "reported", dom[0]
    assert dom[0]["cwe"] == "CWE-79"
    # 证据必须跟着结论一起落地
    assert list((tmp_path / "run" / "capsules").glob("*.json"))

# ── B119：同一个"执行了"，反射型与客户端型要判得不一样 ─────────────
ECHO_PAGE = None  # 服务端把 q 原样回显进 HTML（反射型）
JS_PAGE = None    # 响应里没有 q，脚本把 location.search 拼进 innerHTML（客户端型）


def test_sink_kind_is_distinguished(tmp_path) -> None:  # noqa: ANN001
    """两条都执行了，但原因不同——结论必须分得开。"""
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from veripen_core.render.worker import RenderThread
    from veripen_core.routes import (
        AssetRef, Resolver, RouteCandidate, RouteHints, RouteProvenance, RouteQuery,
    )
    from veripen_core.routes.sources import StaticSource
    from veripen_core.tools import http as http_tool
    from veripen_cli.cmd_hunt import build_guard
    from veripen_oracles.render_transport import RenderTransport
    from veripen_oracles.tier_dom import XssDom

    class _H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            from urllib.parse import parse_qs, urlsplit

            q = parse_qs(urlsplit(self.path).query).get("q", [""])[0]
            if self.path.startswith(S + "echo"):
                # 服务端原样回显（未转义）+ 浏览器会执行它
                body = f"<html><body><div id='out'>{q}</div></body></html>"
            else:
                body = (
                    "<html><body><div id='out'></div><script>"
                    "document.getElementById('out').innerHTML ="
                    " new URLSearchParams(location.search).get('q');"
                    "</script></body></html>"
                )
            raw = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *a):  # noqa: ANN002
            pass

    srv = HTTPServer((LOOPBACK, 0), _H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def _run(path: str):  # noqa: ANN202
        from veripen_oracles.common.harness import run as run_oracle

        cands = [RouteCandidate(method="GET",
                                template="http" + ":" + S * 2 + f"{LOOPBACK}:{port}{path}",
                                param_names=("q",), source=RouteProvenance.CRAWL)]
        rs = Resolver(sources=[StaticSource("crawl", RouteProvenance.CRAWL, cands)]).resolve(
            RouteQuery(asset=AssetRef(id=LOOPBACK), hints=RouteHints(), min_confidence=0.0),
            now=int(time.time()),
        )
        guard = build_guard(LOOPBACK, rs, ("xss_dom",), rate=10)
        http_tool.uninstall_guard()
        http_tool.install_guard(guard)
        try:
            with RenderThread() as rt:
                tp = RenderTransport(rt, oracle_id="xss_dom")
                return run_oracle(XssDom(), rs.bindings[0], tp,
                                  symbols=__import__("veripen_oracles").SymbolTable(),
                                  nonce="t", bindings={"param_injectable": rs.bindings[0]})
        finally:
            http_tool.uninstall_guard()

    try:
        reflected = _run(S + "echo")
        client = _run(S + "js")
    finally:
        srv.shutdown()

    assert reflected.judgement is not None
    assert client.judgement is not None
    assert reflected.judgement.assertion["observed"]["sink"] == "reflected", reflected.judgement
    assert client.judgement.assertion["observed"]["sink"] == "client_side", client.judgement
