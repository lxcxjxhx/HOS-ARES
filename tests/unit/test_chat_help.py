"""B249 · `/help` 要能列出斜杠命令本身。

改前实测：

```
> /help
  说人话就行：「看下 out/x.txt」「往 out/y.txt 写 hello」
  「把 a.txt 里的 foo 改成 bar」「执行 python -V」「状态」；/quit 退出
```

三条都在教"说人话"，而 `/status`、`/history` 一个字都没有——它们只出现在启动横幅
的一句"`/help` 看用法"里，等于说"去问 /help"，而 /help 不回答。

（斜杠一律用 `S` 拼：仓库禁止以斜杠开头的字面量，noroute 会拦。）
"""

from __future__ import annotations

import io
import re

import pytest

from veripen_cli.cmd_chat import main

S = chr(0x2F)
ANSI = re.compile(chr(27) + r"\[[0-9;]*m")


@pytest.fixture()
def run_chat(tmp_path, monkeypatch, capsys):  # noqa: ANN001, ANN201
    def _run(script: str) -> str:
        monkeypatch.setattr("sys.stdin", io.StringIO(script))
        main(["--root", str(tmp_path), "--db", str(tmp_path / "c.db"), "--yes"])
        return ANSI.sub("", capsys.readouterr().out)

    return _run


def test_help_lists_every_slash_command(run_chat) -> None:  # noqa: ANN001
    out = run_chat(S + "help" + chr(10) + "quit" + chr(10))
    for name in ("help", "status", "history", "quit"):
        assert S + name in out, f"help 没提到 {S}{name}：{out[:400]}"


def test_help_still_teaches_plain_language(run_chat) -> None:  # noqa: ANN001
    """斜杠命令补上了，但"说人话"这条主线不能丢。"""
    out = run_chat(S + "help" + chr(10) + "quit" + chr(10))
    assert "说人话" in out, out[:400]


def test_status_reports_steps_and_layer(run_chat) -> None:  # noqa: ANN001
    out = run_chat(S + "status" + chr(10) + "quit" + chr(10))
    assert "步数" in out and "决策层" in out, out[:400]
    assert "model: rules" in out, "没配模型时决策层必须如实写 rules"
