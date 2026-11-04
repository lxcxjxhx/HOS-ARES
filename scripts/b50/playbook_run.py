#!/usr/bin/env python3
"""B50 演练：在本机起一个**最小夹具靶**，把三个 playbook 各跑 N 轮，实测成功率。

为什么要自己起靶：
`success_rate` 只允许"实测值"（31 §1），而本机没有外部靶场。夹具是**合成目标**，
所以产物里显式写 `"rehearsal": true, "fixture": "in-process"`——
这三个数不代表真实资产上的成功率，只证明"资产被真的执行过、数是从执行里数出来的"。

用法：
    python scripts/b50/playbook_run.py --rounds 5
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from veripen_core.playbooks import MEASURED_PATH, load_dir  # noqa: E402

HOST = "127.0.0.1"


class FixtureHandler(BaseHTTPRequestHandler):
    """夹具靶：三个弱点，其中 SQLi 那个**故意不成立**（否则三个数全是 1.0，看不出测量）。"""

    def log_message(self, *_args: Any) -> None:  # 安静
        return

    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/items":
            self._send(200, json.dumps({"ids": ["101", "102"]}).encode())
            return
        if path.startswith("/items/"):
            oid = path.rsplit("/", 1)[-1]
            role = self.headers.get("x-role", "anonymous")
            # 101 越权可读（弱点成立）；102 一律 403（对照组）
            if oid == "101" and role in ("secondary", "primary"):
                self._send(200, json.dumps({"id": "101", "owner": "primary"}).encode())
            else:
                self._send(403, b'{"error":"forbidden"}')
            return
        if path == "/search":
            # 故意**不**做差分：q 与 q' 的响应完全一致 → sqli_bool_diff 应判 REFUTED
            self._send(200, b'{"results":[]}')
            return
        if path == "/api/ping":
            self._send(200, b'{"ok":true}')
            return
        self._send(404, b'{"error":"not_found"}')


def _serve() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer((HOST, 0), FixtureHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://{HOST}:{port}"


def _get(base: str, path: str, *, role: str | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(base + path)
    if role:
        req.add_header("x-role", role)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 - 本机夹具
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _idor_verified(base: str) -> bool:
    """弱点成立 = 低权账号能读到别人的对象，且自己的对象（对照）也通。"""
    status_other, _ = _get(base, "/items/101", role="secondary")
    status_own, _ = _get(base, "/items/101", role="primary")
    return status_other == 200 and status_own == 200


def _sqli_verified(base: str) -> bool:
    """布尔差分成立 = 无害引号与非引号响应**不同**（夹具刻意做成相同）。"""
    a, body_a = _get(base, "/search?q=1")
    b, body_b = _get(base, "/search?q=1%27")
    return a == b == 200 and body_a != body_b


def _ratelimit_absent(base: str) -> bool:
    """限流缺失 = 连发 20 次全通，且没有任何 429。"""
    codes = [_get(base, "/api/ping")[0] for _ in range(20)]
    return all(code == 200 for code in codes)


PROBES = {
    "pb-idor-enum-first": _idor_verified,
    "pb-sqli-waf-probe": _sqli_verified,
    "pb-ratelimit-window": _ratelimit_absent,
}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="playbook-run")
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--out", default=str(MEASURED_PATH))
    args = ap.parse_args(argv)
    if args.rounds < 5:
        print("playbook-run: FAIL: 演练次数必须 ≥5（少于 5 次的成功率没有统计意义）")
        return 2

    books = load_dir()
    unknown = {b.id for b in books} - set(PROBES)
    if unknown:
        print(f"playbook-run: FAIL: 缺演练探针 {sorted(unknown)}")
        return 2

    server, base = _serve()
    try:
        results: dict[str, dict[str, Any]] = {}
        for book in books:
            probe = PROBES[book.id]
            hits = sum(1 for _ in range(args.rounds) if probe(base))
            results[book.id] = {
                "success_rate": round(hits / args.rounds, 4),
                "samples": args.rounds,
                "verdicts": {"VERIFIED": hits, "REFUTED": args.rounds - hits},
                "rehearsal": True,
                "fixture": "in-process",
            }
    finally:
        server.shutdown()

    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for pid, rec in sorted(results.items()):
        print(f"{pid}: success_rate={rec['success_rate']} samples={rec['samples']} (演练夹具)")
    print(f"写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
