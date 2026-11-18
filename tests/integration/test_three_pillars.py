"""B415 · 三支柱端到端体检：strix / 驭智 / codex 各跑一遍，产物齐不齐。

为什么单独一条：`run`、`hunt`、`platform`、`chat` 各有自己的用例，但**没有一条**
证明四者在同一台机器、同一批产物语义下能同时跑通。客户问的是"这台机器上能演示吗"，
不是"四个模块各自的单测绿吗"。

四个入口各断言一件事，其余交给它们自己的用例：
- strix `run`：出 findings.json + report.md，且有 reported 结论（不是全 undetermined）
- strix `hunt`：出 hunt.json，且步数 > 0（决策器真的走了）
- 驭智 `platform`：两资产 → platform-report.md
- codex `chat`：一句话 → 工具真执行（文件真写盘）
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest

SEP = chr(0x2F)


def _spec(base: str) -> str:
    return json.dumps({
        "openapi": "3.0.0", "info": {"title": "lab", "version": "1"},
        "servers": [{"url": base}],
        "paths": {
            f"{SEP}api{SEP}search": {"get": {"x-veripen-tags": ["param_injectable"]}},
            f"{SEP}api{SEP}read": {"get": {"x-veripen-tags": ["file_read"]}},
            f"{SEP}api{SEP}fetch": {"get": {"x-veripen-tags": ["url_fetch"]}},
            f"{SEP}portal{SEP}token": {"get": {"x-veripen-tags": ["token_issue"]}},
        },
    }, ensure_ascii=False)


@pytest.fixture()
def labs() -> Any:
    from lab.app import App
    from lab.net_server import LabServer

    servers = [LabServer(App(secure=False)).start(), LabServer(App(secure=False)).start()]
    try:
        yield servers
    finally:
        for srv in servers:
            srv.stop()


def test_strix_run_produces_findings_and_report(labs: Any, tmp_path: Path) -> None:
    from veripen_cli.cmd_run import main

    spec = tmp_path / "spec.json"
    spec.write_text(_spec(labs[0].base_url), encoding="utf-8")
    out = tmp_path / "run"
    rc = main(["--target", labs[0].base_url, "--openapi", str(spec),
               "--out", str(out), "--qps", "6", "--max-faces", "4"])

    data = json.loads((out / "findings.json").read_text(encoding="utf-8"))
    outcomes = [f["outcome"] for f in data["findings"]]
    assert rc == 0, rc
    assert (out / "report.md").exists()
    assert outcomes.count("reported") >= 1, outcomes


def test_strix_hunt_takes_steps(labs: Any, tmp_path: Path) -> None:
    from veripen_cli.cmd_hunt import main

    spec = tmp_path / "spec.json"
    spec.write_text(_spec(labs[0].base_url), encoding="utf-8")
    out = tmp_path / "hunt"
    rc = main(["--target", labs[0].base_url, "--openapi", str(spec),
               "--out", str(out), "--model", "scripted",
               "--max-steps", "4", "--rate", "6"])

    hunt = json.loads((out / "hunt.json").read_text(encoding="utf-8"))
    assert rc == 0, rc
    assert hunt.get("steps"), "决策器一步都没走"


def test_platform_covers_two_assets(labs: Any, tmp_path: Path) -> None:
    from veripen_cli.cmd_platform import main

    spec = tmp_path / "spec.json"
    spec.write_text(_spec(labs[0].base_url), encoding="utf-8")
    out = tmp_path / "pf"
    rc = main(["--targets", f"{labs[0].base_url},{labs[1].base_url}",
               "--openapi", str(spec), "--out", str(out),
               "--db", str(tmp_path / "pf.db"), "--qps", "6"])

    assert rc == 0, rc
    assert (out / "platform-report.md").exists()
    assert list(out.glob("asset-*")), "没有按资产分目录"


def test_chat_executes_a_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from veripen_cli.cmd_chat import main

    monkeypatch.setattr("sys.stdin", io.StringIO("往 notes.txt 写 hello\n/quit\n"))
    rc = main(["--root", str(tmp_path), "--db", str(tmp_path / "c.db"), "--yes"])

    assert rc == 0, rc
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "hello"


if __name__ == "__main__":  # pragma: no cover - 手工体检入口
    sys.exit(pytest.main([__file__, "-q"]))
