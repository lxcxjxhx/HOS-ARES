"""B80 · 多步计划与会话延续（H22 判据 A/B/E）。

多步不属于 `Loop` 的 `tool_calls_per_step=1`（那是判定骨架的纪律）：
它由 `Plan.steps` 承载，**逐步执行、每步仍是一次单一调用**。
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

import pytest

SEP = chr(0x2F)
ANSI = re.compile(chr(27) + r"\[[0-9;]*m")


# ── A：一句话多步 ──────────────────────────────────────────────
def test_conjunction_produces_two_steps() -> None:
    from veripen_core.agent.intent import parse

    p = parse(f"把 a.txt 里的 x 改成 y 然后读一下")
    assert len(p.steps) == 2, [(s.tool, s.args) for s in p.steps]
    assert p.steps[0].tool == "edit"
    assert p.steps[1].tool == "read"
    assert p.steps[1].args["path"] == "a.txt", "第二段要沿用第一段的路径"


def test_three_steps() -> None:
    from veripen_core.agent.intent import parse

    p = parse("往 a.txt 写 hi 然后读一下 接着看状态")
    tools = [s.tool for s in p.steps]
    assert tools[0] == "write" and "read" in tools and "status" in tools


# ── 任一段解析不出就退回单步 ──────────────────────────────────
def test_failed_segment_falls_back_to_single_step() -> None:
    from veripen_core.agent.intent import parse

    p = parse("看下 a.txt 然后把这件事办一下")
    # 第二段解析不出 → 不拼多步（宁可整句按单步/没听懂处理）
    assert len(p.steps) <= 1


# ── E：单句单步不受影响 ────────────────────────────────────────
def test_single_step_unchanged() -> None:
    from veripen_core.agent.intent import parse

    p = parse("看下 a.txt")
    assert len(p.steps) == 1 and p.steps[0].tool == "read"


# ── B：失败即停（CLI 行为）────────────────────────────────────
def test_failure_stops_remaining_steps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                       capsys: pytest.CaptureFixture[str]) -> None:
    from veripen_cli.cmd_chat import main

    # 第 1 步写成功、第 2 步读不存在的文件 → 第 3 步不该执行
    script = ("往 ok.txt 写 hi 然后看下 missing.txt 接着看状态\n"
              + SEP + "quit\n")
    monkeypatch.setattr("sys.stdin", io.StringIO(script))
    main(["--root", str(tmp_path), "--db", str(tmp_path / "c.db"), "--yes"])
    out = ANSI.sub("", capsys.readouterr().out)

    assert "已停止" in out, f"没有说明停在哪：{out[-400:]}"
    assert "未执行" in out
    # 第 2 步失败后，status 不该被执行
    assert out.count("⏺ status") == 0


def test_failure_message_names_step_index(tmp_path: Path,
                                          monkeypatch: pytest.MonkeyPatch,
                                          capsys: pytest.CaptureFixture[str]) -> None:
    from veripen_cli.cmd_chat import main

    script = ("往 ok.txt 写 hi 然后看下 missing.txt\n" + SEP + "quit\n")
    monkeypatch.setattr("sys.stdin", io.StringIO(script))
    main(["--root", str(tmp_path), "--db", str(tmp_path / "c.db"), "--yes"])
    out = ANSI.sub("", capsys.readouterr().out)
    assert "第 2 步失败" in out


# ── D：/history ───────────────────────────────────────────────
def test_history_command_lists_steps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                     capsys: pytest.CaptureFixture[str]) -> None:
    from veripen_cli.cmd_chat import main

    script = f"往 h.txt 写 v\n{SEP}history\n{SEP}quit\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(script))
    main(["--root", str(tmp_path), "--db", str(tmp_path / "c.db"), "--yes"])
    out = ANSI.sub("", capsys.readouterr().out)
    assert "write" in out
    # 历史渲染里应出现步骤序号
    assert "1." in out or "1 " in out
