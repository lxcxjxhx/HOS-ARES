"""B169：`sh` 跑不动 python 命令——工具看着能跑，其实跑不了。

两处病，串起来让 `sh python …` 全废：

1. `cmd_agent._do_sh` 按首词猜解释器：首词含 `python` 就把**整行**塞进 `python -c`，
   于是 `sh python -V` 变成 `python -c "python -V"`——SyntaxError；
   连"本想给它一段 Python 代码"的 `sh python print(1)` 也带着 `python ` 前缀进 `-c`，同样语法错。
2. 即便把解释器判定改对，Windows 上 argv 形式的 `cmd` 调用会经过
   `list2cmdline`，内层引号被转成反斜杠形式，cmd 不认——`python -c "print(6*7)"`
   **exit=0 且空输出**（最坏的一种：看起来成功）。

修法：`sh` 就是"跑一条命令"，解释器由系统 shell 决定；Windows 上把整行**以字符串**交给
`cmd`（shell 形式），引号语义与用户手敲一致。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

WIN = sys.platform == "win32"


def _sh(tmp_path: Path, arg: str) -> tuple[bool, str]:
    from veripen_cli.cmd_agent import _do_sh

    return _do_sh(tmp_path, arg)


def test_plain_command_still_works(tmp_path: Path) -> None:
    ok, summary = _sh(tmp_path, "echo hi")
    assert ok and "hi" in summary


def test_python_version_is_not_wrapped_into_dash_c(tmp_path: Path) -> None:
    """首词是 python 不等于"这行是 Python 源码"。"""
    ok, summary = _sh(tmp_path, "python -V")
    assert ok, summary
    assert "Python" in summary


@pytest.mark.skipif(not WIN, reason="引号问题只在 cmd 上出现")
def test_python_one_liner_keeps_its_quotes(tmp_path: Path) -> None:
    """`python -c "…"` 必须真的跑到那段代码，而不是静默 exit=0 空输出。"""
    ok, summary = _sh(tmp_path, 'python -c "print(6*7)"')
    assert ok, summary
    assert "42" in summary, summary


def test_failure_still_reports_the_exit_code(tmp_path: Path) -> None:
    ok, summary = _sh(tmp_path, "cmd /c exit 3" if WIN else "sh -c 'exit 3'")
    assert not ok
    assert "exit=3" in summary
