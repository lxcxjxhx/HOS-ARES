"""B111 · 管道的 BOM 与 stdin 编码。

两个真问题：
1. `cmd_agent` 只切了 stdout，没切 stdin——管道进来的第一条指令带着 BOM
   （`read` → `\\ufeffread`），指令模式当场全废；
2. 即使切了，`encoding="utf-8"` 也不吃 BOM，要 `utf-8-sig`。
"""

from __future__ import annotations

import io

import pytest


class _Recorder:
    def __init__(self) -> None:
        self.kwargs: dict[str, object] = {}

    def reconfigure(self, **kwargs: object) -> None:
        self.kwargs.update(kwargs)


def test_setup_stdio_uses_utf8_sig(monkeypatch: pytest.MonkeyPatch) -> None:
    from veripen_cli import _io

    recorder = _Recorder()
    monkeypatch.setattr(_io.sys, "stdin", recorder)
    _io.setup_stdio()
    assert recorder.kwargs.get("encoding") == "utf-8-sig", "BOM 会被当成指令的一部分"


def test_agent_wires_stdin_not_only_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    """回归：cmd_agent 曾只调 setup_stdout，管道输入的首条指令带 BOM。"""
    from veripen_cli import cmd_agent

    called: list[str] = []
    monkeypatch.setattr(cmd_agent, "setup_stdio", lambda: called.append("stdio"))
    monkeypatch.setattr("sys.stdin", io.StringIO("quit\n"))
    cmd_agent.main([])
    assert "stdio" in called, "agent 必须切 stdin，否则管道输入的首行带 BOM"


def test_bom_is_stripped_by_utf8_sig() -> None:
    """行为证据：同样的字节，utf-8 留 BOM，utf-8-sig 剥掉。"""
    raw = "\ufeffread README.md".encode()
    assert raw.decode("utf-8").startswith("\ufeff")
    assert raw.decode("utf-8-sig") == "read README.md"
