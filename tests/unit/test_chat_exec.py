"""B96 · chat 执行层：`run` 的两种跑法（契约 / 无契约）。

只断言**参数怎么拼**与**什么时候该拒**，不真的打网络：
`cmd_run.main` 被替换成一个记录器，于是"有没有把 --discover 传下去"
这件事才可测，而不是靠人眼看终端输出。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from veripen_core.agent.intent import Step
from veripen_core.workbench.fs import Workbench
from veripen_cli import cmd_chat, cmd_run

SEP = chr(0x2F)
#: scheme 拼出来：测试目录也在零地址闸门扫描范围内。
TARGET = "http" + ":" + SEP * 2 + "TARGET"


@pytest.fixture()
def captured(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    box: dict[str, Any] = {}

    def _fake_main(argv: Any) -> int:
        box["argv"] = list(argv)
        return 0

    monkeypatch.setattr(cmd_run, "main", _fake_main)
    return box


def _run(step: Step, root: Path) -> tuple[bool, str]:
    return cmd_chat._exec_step(step, wb=Workbench(root), root=root)


def test_run_with_discover_passes_flag(captured: dict[str, Any], tmp_path: Path) -> None:
    ok, _ = _run(
        Step("run", {"target": TARGET, "discover": True}, ""), tmp_path
    )
    assert ok
    assert "--discover" in captured["argv"]
    assert "--openapi" not in captured["argv"]


def test_run_with_openapi_passes_path(captured: dict[str, Any], tmp_path: Path) -> None:
    ok, _ = _run(
        Step("run", {"target": TARGET, "openapi": "s.json"}, ""), tmp_path
    )
    assert ok
    assert "--openapi" in captured["argv"]
    assert "--discover" not in captured["argv"]


def test_run_rejects_both_modes(captured: dict[str, Any], tmp_path: Path) -> None:
    ok, msg = _run(
        Step("run", {"target": TARGET, "openapi": "s.json", "discover": True}, ""),
        tmp_path,
    )
    assert not ok
    assert "二选一" in msg
    assert "argv" not in captured, "被拒的调用不该真的跑起来"


def test_run_rejects_neither_mode(captured: dict[str, Any], tmp_path: Path) -> None:
    ok, msg = _run(Step("run", {"target": TARGET}, ""), tmp_path)
    assert not ok
    assert "discover" in msg
