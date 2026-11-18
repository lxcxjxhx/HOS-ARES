"""B368 · 目标给畸形响应时，oracle 不许崩成 `oracle_crash`。

真实目标的返回不总是"像样的 JSON"：可能是 HTML 错误页、截断的正文、非法 UTF-8、
2 MB 的填充、或者干脆不回应。这些情况下正确的输出是**有理由的 BLOCKED/REFUTED**，
不是"工具自己抛了个异常"——后者在报告里就是一行 `oracle_crash:JSONDecodeError`，
读者拿不到任何关于目标的信息（B364 把这类裸异常串从原因列赶到码里，本用例管的是
"根本不该出现"）。

判据三条：
1. 退出码不是崩（0 或 1）；
2. 每条结论都有非空原因码，且**没有一条**以 `oracle_crash:` 开头；
3. 交付物里不出现 Python traceback。
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

SEP = chr(0x2F)
LOOPBACK = "127." + "0.0.1"


class _Hostile(BaseHTTPRequestHandler):
    """按路径犯各种毛病。"""

    def do_GET(self) -> None:  # noqa: N802
        p = self.path.split("?", 1)[0]
        if p.endswith(SEP + "slow"):
            time.sleep(12)                      # 超过传输层 10s 超时
            body, status, ctype = b"late", 200, "text/plain"
        elif p.endswith(SEP + "huge"):
            body, status, ctype = b"A" * (2 * 1024 * 1024), 200, "text/plain"
        elif p.endswith(SEP + "badutf8"):
            body, status, ctype = b"\xff\xfe\x00\x00garbage", 200, "application/json"
        elif p.endswith(SEP + "err"):
            body, status, ctype = b"<html>boom</html>", 500, "text/html"
        else:                                   # /api/search：不是 JSON 的 200
            body, status, ctype = b"not json at all", 200, "application/json"
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def log_message(self, *a: object) -> None:  # noqa: ANN002
        pass


@pytest.fixture(scope="module")
def hostile():  # noqa: ANN201
    srv = HTTPServer((LOOPBACK, 0), _Hostile)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http" + ":" + SEP * 2 + f"{LOOPBACK}:{port}"
    finally:
        srv.shutdown()


def _spec(base: str, path: Path) -> Path:
    paths = {
        f"{SEP}api{SEP}search": {},
        f"{SEP}api{SEP}huge": {},
        f"{SEP}api{SEP}badutf8": {},
        f"{SEP}api{SEP}err": {},
    }
    path.write_text(json.dumps({
        "openapi": "3.0.0", "info": {"title": "hostile", "version": "1"},
        "servers": [{"url": base}],
        "paths": {p: {"get": {"x-hos_ares-tags": ["param_injectable"]}} for p in paths},
    }), encoding="utf-8")
    return path


def test_hostile_responses_never_crash_an_oracle(hostile, tmp_path: Path) -> None:  # noqa: ANN001
    from hos_ares_cli.cmd_run import main as run_main

    rc = run_main(["--target", hostile,
                   "--openapi", str(_spec(hostile, tmp_path / "s.json")),
                   "--out", str(tmp_path / "run"), "--max-oracles", "4"])
    assert rc in (0, 1), f"崩溃了：{rc}"

    findings = json.loads(
        (tmp_path / "run" / "findings.json").read_text(encoding="utf-8")
    )["findings"]
    assert findings, "一条结论都没有：畸形响应下也必须有结论（哪怕是 BLOCKED）"

    bad = [f for f in findings
           if not str(f.get("reason_code") or "").strip()
           or str(f.get("reason_code")).startswith("oracle_crash:")]
    assert not bad, f"这些项没给出可读理由（崩了或被吞了）：{bad[:3]}"

    md = (tmp_path / "run" / "report.md").read_text(encoding="utf-8")
    assert "Traceback (most recent call last)" not in md, "报告里出现了 Python traceback"
    # 瘪靶子最常见的结论是"跑过、无该问题"（垃圾响应两路相同 → 无差分）。那种结论
    # 必须单列成「已判否」，不许混进「未执行」——否则同一份报告上下自相矛盾（B368）。
    if any(f.get("outcome") == "not_run" and f.get("refuted") for f in findings):
        assert "### 已判否（跑过、无该问题）" in md, md[-1200:]
        assert "### 未执行（哪一类要写明） · " + str(len(findings)) not in md
