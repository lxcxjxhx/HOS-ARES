"""B160：把"报告里的复现命令真的能跑"固化成端到端契约。

三次踩坑（B149 文件名、B151 模式、B153 目录）都不是算错，是**没有人跑过那条命令**。
这组用例把整条链路钉在一起：

1. 报告里每一条 `veripen replay --capsule … --mode offline` 指向的文件都存在；
2. 那份文件的 `capsule_id` 就是该 finding 的证据指针（不是别人的）；
3. 复现命令的条数 == 可报告结论的条数（不多给、不少给）；
4. 四个出口（findings.json / coverage.json / 控制台 / 报告）对同一个"已报告"数给同一个数；
5. 证据链的环数 == 结论条数（多面之后也成立）。

演示脚本的 report.md 走同一条渲染路径，一并验（B157 修过它写死 `out/capsules` 的问题）。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests", ROOT / "tests" / "unit"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

S = chr(0x2F)
LOOPBACK = "127" + "." + "0" + "." + "0" + "." + "1"
HTTP = "http" + ":" + S * 2
CMD = re.compile(r"veripen replay --capsule (\S+) --mode offline")
HOME = (
    '<html><body><a href="' + S + 'a?x=1">a</a>'
    '<a href="' + S + 'b?x=1">b</a></body></html>'
).encode()


class _H(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body, ctype = b'{"total":0,"items":[]}', "application/json"
        if not (self.path.startswith(S + "a") or self.path.startswith(S + "b")):
            body, ctype = HOME, "text/html"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # noqa: ANN002
        pass


@pytest.fixture(scope="module")
def run_dir(tmp_path_factory: pytest.TempPathFactory):  # noqa: ANN201
    out = tmp_path_factory.mktemp("contract") / "run"
    srv = HTTPServer((LOOPBACK, 0), _H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        from veripen_cli.cmd_run import main

        main(["--target", HTTP + f"{LOOPBACK}:{port}",
              "--discover", "--discover-rounds", "2", "--discover-pages", "8",
              "--out", str(out)])
    finally:
        srv.shutdown()
    return out


def _report(run_dir: Path) -> str:  # noqa: ANN001
    return (run_dir / "report.md").read_text(encoding="utf-8")


def test_every_command_in_the_report_resolves(run_dir) -> None:  # noqa: ANN001
    from veripen_core.evidence import load_capsule

    md = _report(run_dir)
    commands = CMD.findall(md)
    reported = [
        f for f in json.loads((run_dir / "findings.json").read_text(encoding="utf-8"))["findings"]
        if f.get("outcome") == "reported"
    ]
    assert len(commands) == len(reported), "命令条数必须等于可报告结论数"

    evidence = {str((f.get("evidence") or {}).get("capsule_id")): f for f in reported}
    for path in commands:
        # 报告里的路径是**相对报告所在目录**的（B189）：交付物换机器也能读
        p = Path(path)
        if not p.is_absolute():
            p = run_dir / p
        assert p.is_file(), f"报告指向一份不存在的证据：{path}"
        cap = load_capsule(p)
        assert cap.capsule_id in evidence, f"{path} 不是任何一条结论的证据"
        # 指针与文件名必须互相对得上（内容寻址，只有一个合法名字）
        assert p.name == cap.chain.self_sha256 + ".json"


def test_report_states_what_offline_replay_proves(run_dir) -> None:  # noqa: ANN001
    md = _report(run_dir)
    assert "复现口径" in md, "附录必须说明 offline 只重判、不重发"
    assert "不重发请求" in md
    assert "predicate_not_recomputable" in md


def test_counts_agree_across_exits(run_dir) -> None:  # noqa: ANN001
    from web.loader import load_file, summarize

    doc = json.loads((run_dir / "findings.json").read_text(encoding="utf-8"))
    cov = json.loads((run_dir / "coverage.json").read_text(encoding="utf-8"))
    console = summarize(load_file(str(run_dir)))

    reported = doc["counts"]["by_outcome"]["reported"]
    assert cov["summary"]["outcomes"]["reported"] == reported, "覆盖账本与结论数对不上"
    assert console["verified"] == reported, "控制台与结论数对不上"
    assert console["total"] == len(doc["findings"]), "控制台行数与 findings 对不上"


def test_chain_links_equal_reported_findings(run_dir) -> None:  # noqa: ANN001
    from veripen_core.evidence import load_capsule, order_chain, verify_chain

    doc = json.loads((run_dir / "findings.json").read_text(encoding="utf-8"))
    reported = [f for f in doc["findings"] if f.get("outcome") == "reported"]
    caps = [load_capsule(p) for p in sorted((run_dir / "capsules").rglob("*.json"))]
    report = verify_chain(order_chain(caps))
    assert report.ok, report.breaks
    assert report.length == len(reported), "一环一份证据：链长必须等于结论数"


def test_demo_report_commands_resolve(tmp_path: Path) -> None:
    """演示脚本的 report.md 走同一条渲染路径，同样不许给出死链。"""
    from veripen_core.evidence import load_capsule

    out = tmp_path / "demo"
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "demo.py"), "--out", str(out)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=ROOT, timeout=300,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    md = (out / "report.md").read_text(encoding="utf-8")
    commands = CMD.findall(md)
    assert commands, "演示报告里一条复现命令都没有"
    for path in commands:
        p = Path(path)
        assert p.is_file(), f"演示报告指向不存在的证据：{path}"
        assert load_capsule(p).chain.self_sha256 + ".json" == p.name
