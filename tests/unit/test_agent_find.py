"""B171：工作台有 `glob` / `grep`，REPL 却没暴露。

`Workbench` 从 B37 起就有 `glob`（排序、封顶、链接逃逸跳过）和 `grep`
（返回行号与原文，不返回文件全量）。但 `veripen agent` 的指令白名单里只有
read / write / edit / sh ——想找文件只能 `sh dir /s`，想搜内容只能 `sh findstr`。

那正是工作台该替人做的事：用 shell 搜，输出既不受 `MAX_MATCHES` 约束，
也不受工作根边界约束的写法更容易出事。
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests", ROOT / "tests" / "unit"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))


def _root(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("x = 2\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("hello world\n", encoding="utf-8")
    return tmp_path


def _wb(tmp_path: Path):  # noqa: ANN202
    from veripen_core.workbench.fs import Workbench

    return Workbench(tmp_path)


def test_glob_lists_files_sorted(tmp_path: Path) -> None:
    from veripen_cli.cmd_agent import _do_glob

    ok, summary = _do_glob(_wb(_root(tmp_path)), "**/*.py")
    assert ok
    assert summary.startswith("2 个文件"), summary
    assert "src/a.py" in summary


def test_glob_with_no_match_is_not_a_failure(tmp_path: Path) -> None:
    from veripen_cli.cmd_agent import _do_glob

    ok, summary = _do_glob(_wb(_root(tmp_path)), "**/*.rs")
    assert ok and "0 个文件" in summary


def test_glob_keeps_the_scope_guard(tmp_path: Path) -> None:
    """绕过边界不行——`glob` 和 `read` 用的是同一套检查。"""
    from veripen_cli.cmd_agent import _do_glob

    with pytest.raises(Exception):
        _do_glob(_wb(_root(tmp_path)), "../*")


def test_grep_returns_line_numbers(tmp_path: Path) -> None:
    from veripen_cli.cmd_agent import _do_grep

    ok, summary = _do_grep(_wb(_root(tmp_path)), "hello")
    assert ok
    assert summary.startswith("2 处"), summary
    assert "notes.md:1" in summary, "要给路径:行号，不能只给个数"


def test_grep_without_pattern_is_a_usage_error(tmp_path: Path) -> None:
    from veripen_cli.cmd_agent import _do_grep

    ok, summary = _do_grep(_wb(_root(tmp_path)), "  ")
    assert not ok and "用法" in summary


def test_repl_records_glob_and_grep(tmp_path: Path, monkeypatch, capsys) -> None:  # noqa: ANN001
    """两个新指令要真的进白名单、进账本。"""
    from veripen_cli.cmd_agent import main

    _root(tmp_path)
    monkeypatch.setattr(
        "sys.stdin", io.StringIO("glob **/*.py\ngrep hello\nquit\n")
    )
    main(["--db", str(tmp_path / "a.db"), "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert " 1 ok  glob" in out, out[-400:]
    assert " 2 ok  grep" in out
