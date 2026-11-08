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


# ── B148：证据必须绑面，不许两个面共用一份 capsule ────────────────
def test_each_face_gets_its_own_capsule(tmp_path) -> None:  # noqa: ANN001
    """同一 oracle 在多个面上 VERIFIED 时，每条都要有自己的 capsule。

    修前 `index_of` 只按 oracle 建索引，后一个面覆盖前一个，
    于是"面 A 的发现"引用了"面 B 的证据"——replay 会验到错的那一面。
    """
    srv = HTTPServer((LOOPBACK, 0), _H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    out = tmp_path / "run"
    try:
        from veripen_cli.cmd_run import main

        main([
            "--target", HTTP + f"{LOOPBACK}:{port}",
            "--discover", "--discover-rounds", "2", "--discover-pages", "8",
            "--out", str(out),
        ])
    finally:
        srv.shutdown()

    doc = json.loads((out / "findings.json").read_text(encoding="utf-8"))
    reported = [f for f in doc["findings"] if f.get("outcome") == "reported"]
    assert len(reported) >= 2, "这条用例要至少两个面才有意义"

    pairs = {}
    for f in reported:
        ev = f.get("evidence") or {}
        cid = ev.get("capsule_id")
        assert cid, f"{f['id']} 没有 capsule"
        pairs[f["id"]] = cid

    # 同一 oracle 的两个面：capsule 必须不同
    by_oracle: dict[str, set[str]] = {}
    for f in reported:
        by_oracle.setdefault(str(f["oracle"]), set()).add(
            str((f.get("evidence") or {}).get("capsule_id"))
        )
    for oracle, ids in by_oracle.items():
        faces = sum(1 for f in reported if f["oracle"] == oracle)
        assert len(ids) == faces, f"{oracle} 有 {faces} 个面却只有 {len(ids)} 份 capsule"

    # 并且每份 capsule 的 route.ref 要与它自己的面一致
    from veripen_core.evidence import load_capsule

    for f in reported:
        cid = str((f.get("evidence") or {}).get("capsule_id"))
        # 证据按运行分子目录（B153）：按文件名找，别假设是平的
        matches = list((out / "capsules").rglob(cid.split(":")[-1] + ".json"))
        assert matches, f"{f['id']} 的 capsule 在盘上找不到"
        cap = load_capsule(str(matches[0]))
        assert str(cap.route.ref) == str((f.get("route") or {}).get("ref")), f["id"]
