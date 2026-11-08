"""B170：工作台的 `write` 分不清"新建"和"覆盖"。

覆盖不可逆——`Workbench.write` 自己的规矩就是"默认不覆盖，覆盖要显式说"。
但 REPL 里 `_do_write` 一律 `overwrite=True`，而且摘要只写 `写入 N B`：
账本上看不出这一步是**新建了一个文件**，还是**把别人的文件换掉了**。

`WriteResult` 本来就带 `created`（还有本次新增的 `bytes_before`），
丢的是"出口没说出来"这一环。
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests", ROOT / "tests" / "unit"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))


def _wb(tmp_path: Path):  # noqa: ANN202
    from veripen_core.workbench.fs import Workbench

    return Workbench(tmp_path)


def test_create_and_overwrite_read_differently(tmp_path: Path) -> None:
    from veripen_cli.cmd_agent import _do_write

    wb = _wb(tmp_path)
    ok1, first = _do_write(wb, "a.txt hello")
    ok2, second = _do_write(wb, "a.txt hello world!")
    assert ok1 and ok2
    assert "新建" in first and "覆盖" not in first
    assert "覆盖" in second and "原 5B" in second, second


def test_bytes_before_is_recorded_by_the_workbench(tmp_path: Path) -> None:
    wb = _wb(tmp_path)
    wb.write("b.txt", "0123456789")
    assert wb.write("b.txt", "x", overwrite=True).bytes_before == 10
    assert wb.write("c.txt", "x").bytes_before is None


def test_session_history_keeps_the_distinction(tmp_path: Path, monkeypatch, capsys) -> None:  # noqa: ANN001
    """回放历史时也要分得清：这是审计用的那一层。

    REPL 打印的是 `turn.summary`——即**记进会话**的那句话，所以断言 stdout
    等于断言账本里存的内容。
    """
    from veripen_cli.cmd_agent import main

    monkeypatch.setattr("sys.stdin", io.StringIO("write h.txt one\nwrite h.txt two\nquit\n"))
    main(["--db", str(tmp_path / "a.db"), "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert "新建" in out and "覆盖" in out, out[-400:]
