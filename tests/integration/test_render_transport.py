"""B116 · 渲染传输：把浏览器包装成 oracle 能用的 ``send()``。

判定 DOM XSS 的依据是"脚本有没有真跑"，HTTP 响应里看不出来。这里验证两件事：
1. 标记位只有**真执行**才写值（有洞的页面 vs 安全页面）；
2. trace 里不出现地址（只留 route_ref 与 path）。
"""

from __future__ import annotations

import concurrent.futures
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from veripen_core.render import BrowserRenderer
from veripen_oracles.render_transport import MARKER_JS

S = chr(0x2F)
LOOPBACK = "127" + "." + "0" + "." + "0" + "." + "1"
HTTP = "http" + ":" + S * 2

VULN = (
    "<html><body><div id='out'></div><script>"
    "document.getElementById('out').innerHTML ="
    " new URLSearchParams(location.search).get('q') || '';"
    "</script></body></html>"
)
SAFE = (
    "<html><body><div id='out'></div><script>"
    "document.getElementById('out').textContent ="
    " new URLSearchParams(location.search).get('q') || '';"
    "</script></body></html>"
)
PAYLOAD = '<img src=x onerror="window.__vp_xss=1">'


def _available() -> bool:
    return BrowserRenderer().available()


needs_browser = pytest.mark.skipif(not _available(), reason="playwright 不可用：渲染未验")




def _off_loop(fn):  # noqa: ANN001, ANN202
    """在没有事件循环的线程里跑（playwright sync API 的要求）。"""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(fn).result()

class _H(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = (VULN if self.path.startswith(S + "vuln") else SAFE).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # noqa: ANN002
        pass


@pytest.fixture(scope="module")
def site():  # noqa: ANN201
    srv = HTTPServer((LOOPBACK, 0), _H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield srv, port
    finally:
        srv.shutdown()


@needs_browser
def test_marker_fires_only_when_script_executes(site) -> None:  # noqa: ANN001
    srv, port = site

    def _go():  # noqa: ANN202
        with BrowserRenderer() as renderer:
            bad = renderer.capture(
                url=HTTP + f"{LOOPBACK}:{port}{S}vuln?q={PAYLOAD}", marker=MARKER_JS
            )
            ok = renderer.capture(
                url=HTTP + f"{LOOPBACK}:{port}{S}safe?q={PAYLOAD}", marker=MARKER_JS
            )
        return bad, ok

    cap_bad, cap_ok = _off_loop(_go)
    assert cap_bad.marker == "1", "有洞的页面应该真的执行了载荷"
    assert cap_ok.marker == "", "安全页面只是文本，标记位不该动"


@needs_browser
def test_trace_carries_marker_but_no_address(site) -> None:  # noqa: ANN001
    srv, port = site

    class _Binding:
        ref = "route://crawl/deadbeef"
        template = HTTP + f"{LOOPBACK}:{port}{S}vuln?q={{p1}}"

    from veripen_core.tools import http as http_tool
    from veripen_oracles.render_transport import RenderTransport

    def _go():  # noqa: ANN202
      with BrowserRenderer() as renderer:
        # 闸门：手工搭一个，避免依赖完整 resolver
        from veripen_cli.cmd_hunt import build_guard
        from veripen_core.routes import AssetRef, Resolver, RouteCandidate, RouteHints
        from veripen_core.routes import RouteProvenance, RouteQuery
        from veripen_core.routes.sources import StaticSource

        cands = [RouteCandidate(method="GET", template=_Binding.template,
                                source=RouteProvenance.CRAWL)]
        rs = Resolver(sources=[StaticSource("crawl", RouteProvenance.CRAWL, cands)]).resolve(
            RouteQuery(asset=AssetRef(id=LOOPBACK), hints=RouteHints(), min_confidence=0.0),
            now=int(__import__("time").time()),
        )
        guard = build_guard(LOOPBACK, rs, ("xss_dom",), rate=10)
        http_tool.uninstall_guard()
        http_tool.install_guard(guard)
        try:
            transport = RenderTransport(renderer, oracle_id="xss_dom")
            trace = transport.send(rs.bindings[0], {"p1": PAYLOAD}, "probe")
        finally:
            http_tool.uninstall_guard()
        return trace

    trace = _off_loop(_go)
    data = json.loads(trace.body)
    assert data["marker"] == "1"
    assert str(port) not in trace.body.decode("utf-8"), "trace 里不许出现端口/地址"
    assert trace.route_ref.startswith("route" + ":" + S * 2)
