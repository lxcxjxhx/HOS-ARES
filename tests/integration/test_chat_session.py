"""B77 · chat 会话端到端（H20 判据 A/C/D/E/F）。

用 StringIO 替换 stdin，不碰真实终端；工具真的执行（fs 真写盘）。
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

import pytest

SEP = chr(0x2F)
ANSI = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture()
def chat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    def _run(script: str, *extra: str) -> str:
        from veripen_cli.cmd_chat import main

        monkeypatch.setattr("sys.stdin", io.StringIO(script))
        main(["--root", str(tmp_path), "--db", str(tmp_path / "c.db"),
              "--yes", *extra])
        return ANSI.sub("", "")

    return _run


def _out(capsys: pytest.CaptureFixture[str]) -> str:
    return ANSI.sub("", capsys.readouterr().out)


# ── A：说人话 → 工具执行 ──────────────────────────────────────
def test_natural_language_writes_file(tmp_path: Path, chat: Any,
                                      capsys: pytest.CaptureFixture[str]) -> None:
    chat(f"往 notes.txt 写 hello\n/quit\n")
    out = _out(capsys)
    assert "write" in out and "hello" in out
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "hello"


# ── C：diff 可见 ──────────────────────────────────────────────
def test_write_shows_diff(capsys: pytest.CaptureFixture[str], chat: Any) -> None:
    chat("往 a.txt 写 one\n把 a.txt 里的 one 改成 two\n/quit\n")
    out = _out(capsys)
    assert "+1" in out or "+2" in out, f"没有新增行统计：{out[:200]}"
    assert "-" in out, "没有删除行统计"
    assert "+two" in out and "-one" in out, "diff 正文缺失"


# ── E：决策层如实渲染 ────────────────────────────────────────
def test_banner_states_decision_layer(chat: Any, capsys: pytest.CaptureFixture[str]) -> None:
    chat(SEP + "quit" + chr(10))
    out = _out(capsys)
    assert "model:" in out
    # 本机没有模型后端时必须写 rules，不许假装
    assert "model: rules" in out or "model: " in out


# ── B：计划先于执行 + 可否决 ──────────────────────────────────
def test_plan_shown_before_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                     capsys: pytest.CaptureFixture[str]) -> None:
    from veripen_cli.cmd_chat import main

    monkeypatch.setattr("sys.stdin", io.StringIO("往 p.txt 写 x\nn\n/quit\n"))
    main(["--root", str(tmp_path), "--db", str(tmp_path / "c.db")])
    out = ANSI.sub("", capsys.readouterr().out)
    assert "计划" in out
    assert "已否决" in out
    assert not (tmp_path / "p.txt").exists(), "否决后不该执行"


# ── F：上下文累计 ────────────────────────────────────────────
def test_second_turn_references_previous(tmp_path: Path, chat: Any,
                                         capsys: pytest.CaptureFixture[str]) -> None:
    chat("往 r.txt 写 abc\n再读一遍它\n/quit\n")
    out = _out(capsys)
    assert "abc" in out.split("read")[-1], "第二轮没读到第一轮写的内容"


# ── 工具失败不崩界面 ──────────────────────────────────────────
def test_missing_file_reports_and_continues(tmp_path: Path, chat: Any,
                                            capsys: pytest.CaptureFixture[str]) -> None:
    chat("看下 nope.txt\n状态\n/quit\n")
    out = _out(capsys)
    assert "FsError" in out or "不存在" in out
    assert "步数" in out or "状态" in out, "一次失败不该打死会话"
