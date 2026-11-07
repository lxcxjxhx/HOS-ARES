"""B121 · hunt 多面：route_ref 生效 + 游标推进 + 跑完显式说 all_faces_done。"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

S = chr(0x2F)
LOOPBACK = "127" + "." + "0" + "." + "0" + "." + "1"
HTTP = "http" + ":" + S * 2


class _H(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b'{"total":0,"items":[]}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # noqa: ANN002
        pass


def _runtime(port: int):  # noqa: ANN202
    from veripen_core.routes import (
        AssetRef, Resolver, RouteCandidate, RouteHints, RouteProvenance, RouteQuery,
    )
    from veripen_core.routes.sources import StaticSource
    from veripen_cli.cmd_hunt import HuntRuntime
    from veripen_oracles.live import CoreTransport

    cands = [
        RouteCandidate(method="GET", template=HTTP + f"{LOOPBACK}:{port}{S}a",
                       param_names=("x",), source=RouteProvenance.CRAWL,
                       response_shape={"semantic_tags": ["param_injectable"]}),
        RouteCandidate(method="GET", template=HTTP + f"{LOOPBACK}:{port}{S}b",
                       param_names=("x",), source=RouteProvenance.CRAWL,
                       response_shape={"semantic_tags": ["param_injectable"]}),
    ]
    rs = Resolver(sources=[StaticSource("crawl", RouteProvenance.CRAWL, cands)]).resolve(
        RouteQuery(asset=AssetRef(id=LOOPBACK), hints=RouteHints(), min_confidence=0.0),
        now=int(time.time()),
    )
    return HuntRuntime(rs, CoreTransport(timeout_s=10.0), __import__("veripen_oracles").SymbolTable())


def test_cursor_walks_faces_then_stops_explicitly() -> None:
    from veripen_core.tools import http as http_tool
    from veripen_cli.cmd_hunt import build_guard

    srv = HTTPServer((LOOPBACK, 0), _H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        rt = _runtime(port)
        guard = build_guard(LOOPBACK, rt.routeset, ("sqli_bool_diff",), rate=20)
        http_tool.uninstall_guard()
        http_tool.install_guard(guard)
        try:
            first = rt.run_oracle("sqli_bool_diff", "", None)
            second = rt.run_oracle("sqli_bool_diff", "", None)
            third = rt.run_oracle("sqli_bool_diff", "", None)
        finally:
            http_tool.uninstall_guard()
    finally:
        srv.shutdown()

    assert first["face_index"] == 1 and first["faces_total"] == 2, first
    assert second["face_index"] == 2, second
    assert first["face"] != second["face"], "两次应落在不同的面"
    assert third["reason"] == "all_faces_done", third
    assert len(rt.faces_run) == 2


def test_explicit_route_ref_wins() -> None:
    """`route_ref` 这个参数以前被完全忽略——模型选了面也不生效。"""
    from veripen_core.tools import http as http_tool
    from veripen_cli.cmd_hunt import build_guard

    srv = HTTPServer((LOOPBACK, 0), _H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        rt = _runtime(port)
        target = [b for b in rt.routeset.bindings if b.template.endswith(S + "b")][0]
        guard = build_guard(LOOPBACK, rt.routeset, ("sqli_bool_diff",), rate=20)
        http_tool.uninstall_guard()
        http_tool.install_guard(guard)
        try:
            out = rt.run_oracle("sqli_bool_diff", target.ref, None)
        finally:
            http_tool.uninstall_guard()
    finally:
        srv.shutdown()

    # face_index 是"在适用面列表里的位置"，列表顺序由 routeset 决定——
    # 断言用位置对它，而不是假设 b 一定是第 2 条。
    faces = [b for b in rt.routeset.bindings if b.template.endswith(S + "b")]
    assert faces, rt.routeset.bindings
    expected_index = [
        b.ref for b in rt.routeset.bindings
        if "param_injectable" in b.semantic_tags
    ].index(target.ref) + 1
    assert out["face_index"] == expected_index, out
    assert out["face"].endswith(S + "b"), out


def test_unknown_route_ref_is_reported_not_ignored() -> None:
    from veripen_core.tools import http as http_tool
    from veripen_cli.cmd_hunt import build_guard

    srv = HTTPServer((LOOPBACK, 0), _H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        rt = _runtime(port)
        guard = build_guard(LOOPBACK, rt.routeset, ("sqli_bool_diff",), rate=20)
        http_tool.uninstall_guard()
        http_tool.install_guard(guard)
        try:
            out = rt.run_oracle("sqli_bool_diff", "route" + ":" + S * 2 + "crawl/nope", None)
        finally:
            http_tool.uninstall_guard()
    finally:
        srv.shutdown()

    assert out["reason"] == "route_ref_not_found", out
