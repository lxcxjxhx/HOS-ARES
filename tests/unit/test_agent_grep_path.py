"""B308 · agent 的 `grep` 要支持 `grep <正则> [路径]`。

实测（改前）：`grep strict pyproject.toml` 被当成"搜字面量 `strict pyproject.toml`"，
得到 `0 处匹配（pattern=strict pyproject.toml）` 并报 **ok**——用户以为文件里没这个字符串，
其实是他给的路径被吞进了正则。
"""

from __future__ import annotations

import pytest

from hos_ares_cli.cmd_agent import _split_grep_arg


@pytest.mark.parametrize(("arg", "pattern", "glob"), [
    ("strict pyproject.toml", "strict", "pyproject.toml"),
    ("requires docs/*.md", "requires", "docs/*.md"),
    ("def main", "def main", "**/*"),          # 正则里带空格是常见的，别拆
    ("strict", "strict", "**/*"),
    ("", "", "**/*"),
])
def test_split(arg: str, pattern: str, glob: str) -> None:
    assert _split_grep_arg(arg) == (pattern, glob)


def test_grep_with_a_path_scopes_the_search(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    from hos_ares_core.workbench.fs import Workbench

    from hos_ares_cli.cmd_agent import _do_grep

    (tmp_path / "a.txt").write_text("needle\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("needle\n", encoding="utf-8")
    wb = Workbench(root=tmp_path)
    ok, summary = _do_grep(wb, "needle a.txt")
    assert ok
    assert "a.txt" in summary and "b.txt" not in summary, summary
    assert "范围 a.txt" in summary or summary.startswith("1 处"), summary
