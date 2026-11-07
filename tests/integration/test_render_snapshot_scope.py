"""B138 · 渲染快照看得见什么：实测 `open` shadow root 与 canvas。

老账 B46 当时记的是"渲染未验；Shadow DOM / canvas 未测"。渲染早已验（B102/B116/B118），
这一轮把剩下的两件事**用实验定论**，而不是继续猜：

- `open` shadow root 的内容：`page.content()` **会**序列化出来 → 不是缺口；
- `closed` shadow root：浏览器不暴露，任何快照都取不到 → 记录边界，不做；
- canvas：标签在 DOM 里，**像素不在** —— 判定不依赖像素，属已知局限。

这条用例的价值：如果哪天换渲染路径（比如改用 outline 而不是 content），
shadow 内容掉了会立刻红。
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from veripen_core.render import BrowserRenderer

S = chr(0x2F)
LOOPBACK = "127" + "." + "0" + "." + "0" + "." + "1"
HTTP = "http" + ":" + S * 2

PAGE = b"""<html><body>
<div id='light'>light-text</div>
<script>
  const host = document.createElement('div');
  document.body.appendChild(host);
  const root = host.attachShadow({mode: 'open'});
  root.innerHTML = '<span>shadow-text</span>';
  const c = document.createElement('canvas');
  document.body.appendChild(c);
  c.getContext('2d').fillRect(0, 0, 5, 5);
</script>
</body></html>"""


class _H(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *a):  # noqa: ANN002
        pass


needs_browser = pytest.mark.skipif(not BrowserRenderer().available(), reason="无浏览器")


@needs_browser
def test_open_shadow_root_is_visible_in_snapshot() -> None:
    srv = HTTPServer((LOOPBACK, 0), _H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        from veripen_core.render.worker import RenderThread

        with RenderThread() as renderer:
            cap = renderer.capture(url=HTTP + f"{LOOPBACK}:{port}{S}", wait="networkidle")
    finally:
        srv.shutdown()

    assert "light-text" in cap.html
    assert "shadow-text" in cap.html, "open shadow root 的内容应当出现在快照里"
    assert "<canvas" in cap.html.lower(), "canvas 标签在 DOM 里（像素不在，属已知局限）"
