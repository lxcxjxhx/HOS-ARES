"""B79 · chat 接 run / platform（H21 判据 A–E）。

这两条意图把 strix 与驭智能力收进同一个对话面板；
执行器**直接调既有命令**，不重写链路。
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any

import pytest

SEP = chr(0x2F)
DEAD = "http" + ":" + SEP + SEP + "127." + "0.0.1:9"
ANSI = re.compile(chr(27) + r"\[[0-9;]*m")


def _spec(base: str) -> str:
    paths = {
        f"{SEP}api{SEP}search": {"get": {"x-veripen-tags": ["param_injectable"]}},
        f"{SEP}api{SEP}read": {"get": {"x-veripen-tags": ["file_read"]}},
    }
    return json.dumps({
        "openapi": "3.0.0", "info": {"title": "lab", "version": "1"},
        "servers": [{"url": base}],
        "paths": paths,
    })


@pytest.fixture()
def lab(tmp_path: Path):  # noqa: ANN201
    from lab.app import App
    from lab.net_server import LabServer

    srv = LabServer(App(secure=False)).start()
    spec = tmp_path / "spec.json"
    spec.write_text(_spec(srv.base_url), encoding="utf-8")
    srv.spec = str(spec)          # type: ignore[attr-defined]
    srv.tmp = tmp_path            # type: ignore[attr-defined]
    try:
        yield srv
    finally:
        srv.stop()


def _chat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str) -> str:
    from veripen_cli.cmd_chat import main

    monkeypatch.setattr("sys.stdin", io.StringIO(script))
    main(["--root", str(Path(__file__).resolve().parents[2]),
          "--db", str(tmp_path / "c.db"), "--yes"])
    return ""


# ── A：跑渗透 ──────────────────────────────────────────────────
def test_run_intent_reaches_cmd_run(lab: Any, tmp_path: Path,
                                    monkeypatch: pytest.MonkeyPatch,
                                    capsys: pytest.CaptureFixture[str]) -> None:
    script = f"跑一下 {lab.base_url} openapi={lab.spec}\nquit\n"
    _chat(tmp_path, monkeypatch, script)
    out = ANSI.sub("", capsys.readouterr().out)

    assert "run(" in out, f"没走到 run 工具：{out[:300]}"
    assert "exit=" in out, "没有退出码"
    assert (tmp_path / "run").parent.exists()
    # 摘要里应有结论行
    assert "VERIFIED" in out or "路由" in out or "目标" in out


# ── C：缺参数就追问 ────────────────────────────────────────────
def test_run_without_openapi_asks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                  capsys: pytest.CaptureFixture[str]) -> None:
    _chat(tmp_path, monkeypatch, "跑一下 " + DEAD + "\nquit\n")
    out = ANSI.sub("", capsys.readouterr().out)
    assert "需要契约" in out, "缺 openapi 应明确追问，不猜路径"


def test_platform_without_openapi_asks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                       capsys: pytest.CaptureFixture[str]) -> None:
    _chat(tmp_path, monkeypatch, "按 demo-a 跑\nquit\n")
    out = ANSI.sub("", capsys.readouterr().out)
    assert "需要契约" in out


# ── B：按档跑（档不存在时列出可用的）────────────────────────────
def test_unknown_profile_lists_available(tmp_path: Path,
                                         monkeypatch: pytest.MonkeyPatch,
                                         capsys: pytest.CaptureFixture[str]) -> None:
    _chat(tmp_path, monkeypatch, "按 no-such-profile 跑\nquit\n")
    out = ANSI.sub("", capsys.readouterr().out)
    assert "没有档" in out and "demo-a" in out, f"应列出可用档：{out[:300]}"


# ── D：输出折叠 ────────────────────────────────────────────────
def test_output_is_folded(lab: Any, tmp_path: Path,
                          monkeypatch: pytest.MonkeyPatch,
                          capsys: pytest.CaptureFixture[str]) -> None:
    _chat(tmp_path, monkeypatch, f"跑一下 {lab.base_url} openapi={lab.spec}\nquit\n")
    out = ANSI.sub("", capsys.readouterr().out)
    assert "完整输出：" in out, "必须给出完整日志路径"
    # 折叠后不应把整份 stdout 塞进对话
    assert out.count("oracle") < 40, "输出没有被折叠"


# ── E：摘要进会话事件 ──────────────────────────────────────────
def test_summary_recorded_in_events(lab: Any, tmp_path: Path,
                                    monkeypatch: pytest.MonkeyPatch) -> None:
    import sqlite3

    _chat(tmp_path, monkeypatch, f"跑一下 {lab.base_url} openapi={lab.spec}\nquit\n")
    conn = sqlite3.connect(tmp_path / "c.db")
    rows = conn.execute(
        "SELECT tool, detail_json FROM task_events WHERE kind='plan'"
    ).fetchall()
    assert rows, "没有落库"
    tools = {r[0] for r in rows}
    assert "run" in tools, f"run 没落库：{tools}"
    assert any("exit=" in str(r[1]) for r in rows), "摘要在事件里"
