"""B67 · 交互会话（H13 判据 A–D）。

只测会话语义与 CLI 冒烟，不测 TUI 渲染（那是 B56 的范围）。
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from veripen_core.agent.session import COMMANDS, Session
from veripen_core.store import Store


def _store() -> Store:
    return Store(":memory:").init()


# ── A：连续指令的上下文累计 ────────────────────────────────────
def test_turns_accumulate_context() -> None:
    sess = Session(store=_store())
    sess.record(instruction="write a.txt hello", tool="write", ok=True, summary="a.txt 写入 5B")
    sess.record(instruction="read a.txt", tool="read", ok=True, summary="a.txt 5B :: hello")

    facts = sess.context_facts()
    assert len(facts) == 2
    assert facts[0].startswith("write:")
    assert "hello" in facts[1], "第 2 条指令要能看到第 1 条的结果"


def test_failed_turn_not_in_context() -> None:
    sess = Session(store=_store())
    sess.record(instruction="read nope", tool="read", ok=False, summary="文件不存在")
    assert sess.context_facts() == [], "失败的动作不进上下文，避免污染下一步"


# ── B：每步可读 ────────────────────────────────────────────────
def test_each_turn_renders_tool_and_result() -> None:
    sess = Session(store=_store())
    sess.record(instruction="write a.txt x", tool="write", ok=True, summary="a.txt 写入 1B")
    text = sess.render_history()
    assert "write" in text and "a.txt" in text
    assert text.count("\n") == 0, "单步不应多行"


def test_commands_are_whitelisted() -> None:
    assert "read" in COMMANDS and "write" in COMMANDS and "edit" in COMMANDS
    assert "delete" not in COMMANDS, "没有删除能力：方法不存在即能力不存在"


# ── C：中断不丢 + 恢复 ────────────────────────────────────────
def test_resume_reads_back_events(tmp_path: Path) -> None:
    db = tmp_path / "agent.db"
    store = Store(str(db)).init()
    sess = Session(store=store)
    task_id = sess.task_id
    sess.record(instruction="write a.txt hi", tool="write", ok=True, summary="a.txt 写入 2B")
    sess.record(instruction="sh echo hi", tool="sh", ok=False, summary="exit=1 :: ")

    back = Session.resume(Store(str(db)).init(), task_id)
    assert len(back.turns) == 2
    assert back.turns[0].tool == "write" and back.turns[0].ok is True
    assert back.turns[1].ok is False, "失败的步骤也要恢复（中断前的所有痕迹都算数）"


def test_resume_unknown_task_is_empty(tmp_path: Path) -> None:
    back = Session.resume(Store(str(tmp_path / "x.db")).init(), "no-such-task")
    assert back.turns == []


# ── D：无模型要明说 ────────────────────────────────────────────
def test_cli_says_no_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                           capsys: pytest.CaptureFixture[str]) -> None:
    from veripen_cli.cmd_agent import main

    monkeypatch.setattr("sys.stdin", io.StringIO("write t.txt hi\nquit\n"))
    rc = main(["--db", str(tmp_path / "a.db"), "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "未接入" in out, "没接模型就要明说，不能假装调过"
    assert (tmp_path / "t.txt").read_text(encoding="utf-8") == "hi"


def test_cli_rejects_unknown_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                     capsys: pytest.CaptureFixture[str]) -> None:
    from veripen_cli.cmd_agent import main

    monkeypatch.setattr("sys.stdin", io.StringIO("delete t.txt\nquit\n"))
    main(["--db", str(tmp_path / "a.db"), "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert "未知指令" in out
