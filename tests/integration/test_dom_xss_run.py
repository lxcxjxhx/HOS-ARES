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
