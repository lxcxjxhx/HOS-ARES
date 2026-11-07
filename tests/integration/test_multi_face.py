"""B120 · 一个 oracle 跑每条适用的面。

此前 `bindings[tag]` 只取第一个命中的绑定，其余面永远记 `face_without_oracle`——
发现出来的面明明可判，却只判了一条。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

S = chr(0x2F)
LOOPBACK = "127" + "." + "0" + "." + "0" + "." + "1"
HTTP = "http" + ":" + S * 2

HOME = (
    '<html><body><a href="' + S + 'a?x=1">a</a>'
    '<a href="' + S + 'b?x=1">b</a></body></html>'
).encode()


class _H(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b'{"total":0,"items":[]}'
        ctype = "application/json"
        if not (self.path.startswith(S + "a") or self.path.startswith(S + "b")):
            body, ctype = HOME, "text/html"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # noqa: ANN002
        pass


def test_every_applicable_face_is_run(tmp_path) -> None:  # noqa: ANN001
    srv = HTTPServer((LOOPBACK, 0), _H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        from veripen_cli.cmd_run import main

        main([
            "--target", HTTP + f"{LOOPBACK}:{port}",
            "--discover", "--discover-rounds", "2", "--discover-pages", "8",
            "--out", str(tmp_path / "run"),
        ])
    finally:
        srv.shutdown()

    doc = json.loads((tmp_path / "run" / "findings.json").read_text(encoding="utf-8"))
    by_oracle: dict[str, set[str]] = {}
    for f in doc["findings"]:
        by_oracle.setdefault(f["oracle"], set()).add(f["route"]["ref"])
    # 至少一个 oracle 在两条不同面上都跑过
    assert any(len(refs) >= 2 for refs in by_oracle.values()), by_oracle

    cov = json.loads((tmp_path / "run" / "coverage.json").read_text(encoding="utf-8"))
    assert cov["summary"]["executed"] >= 4, cov["summary"]
    assert cov["summary"]["faces_covered"] >= 2, cov["summary"]
