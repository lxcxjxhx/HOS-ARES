"""B229 · `--help` 的 usage 行只许出现**真入口**。

客户的路径是**抄 usage 行**：看到的第一个词就该是他能敲的那个。

而实测曾长这样（13 个子命令里 6 个是错的）：

```
usage: __main__.py [-h] [--root ROOT] …          # doctor：没写 prog，按 sys.argv[0] 推
usage: parity [-h] …                              # 真入口是 veripen parity
usage: veripen coverage-matrix [-h] …             # 子命令叫 coverage，这个不存在
usage: veripen-console [-h] …                     # 只有 veripen 一个 console script
```

`__main__.py` 最糟：既不是命令也不是路径，抄下来直接跑不了。

本用例按**真实输出**断言（不 grep 源码里的字面量）：逐个 import 并跑
`main(["--help"])`，取第一行 usage。内部脚手架按 `python -m …` 断言——
那是 Makefile 真正调它的形式。
"""

from __future__ import annotations

import contextlib
import importlib
import io

import pytest

from veripen_cli.__main__ import COMMANDS, INTERNAL_MODULES


def _usage_line(module_path: str) -> str:
    """跑一次 `main(["--help"])`，返回首行 usage。"""
    mod = importlib.import_module(module_path)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), pytest.raises(SystemExit) as ei:
        mod.main(["--help"])
    assert ei.value.code == 0, f"{module_path} --help 退出了 {ei.value.code}"
    for line in buf.getvalue().splitlines():
        if line.strip().startswith("usage:"):
            return line.strip()
    raise AssertionError(f"{module_path} 的 --help 没有 usage 行")


@pytest.mark.parametrize("name,module_path,_summary", COMMANDS)
def test_subcommand_usage_names_the_real_entry(name: str, module_path: str, _summary: str) -> None:
    """13 个子命令的 usage 首词必须是 `veripen <名字>`。"""
    usage = _usage_line(module_path)
    head = f"usage: veripen {name}"
    assert usage == head or usage.startswith(head + " "), usage


@pytest.mark.parametrize("module_path,_why", sorted(INTERNAL_MODULES.items()))
def test_internal_tool_usage_names_module_invocation(module_path: str, _why: str) -> None:
    """内部脚手架不是子命令：usage 要写 `python -m …`，别写一个不存在的 `veripen` 子命令。"""
    usage = _usage_line(module_path)
    head = f"usage: python -m {module_path}"
    assert usage == head or usage.startswith(head + " "), usage


def test_every_command_module_exists_and_has_main() -> None:
    """COMMANDS 里点名的模块都得真有 `main(argv)`（拼错模块名同样抄不出命令）。"""
    for _name, module_path, summary in COMMANDS:
        mod = importlib.import_module(module_path)
        assert callable(getattr(mod, "main", None)), f"{module_path} 没有 main"
        assert summary.strip(), f"{module_path} 的一句话说明是空的"


# ── B261：`veripen help <子命令>` 要给出那个子命令的帮助 ────────────
def _run(argv: list[str]) -> tuple[int, str]:
    import contextlib

    from veripen_cli.__main__ import main

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        try:
            code = main(argv)
        except SystemExit as exc:      # 子命令的 argparse --help 会抛
            code = int(exc.code or 0)
    return code, buf.getvalue()


def test_help_with_a_subcommand_shows_that_subcommand_usage() -> None:
    code, out = _run(["help", "doctor"])
    assert code == 0, code
    assert "usage: veripen doctor" in out, out[:300]


def test_help_without_a_subcommand_still_shows_the_index() -> None:
    code, out = _run(["help"])
    assert code == 0
    assert "子命令：" in out and "veripen chat" in out


def test_help_with_an_unknown_subcommand_is_an_error() -> None:
    code, _out = _run(["help", "no_such_cmd"])
    assert code == 2, code


def test_usage_index_mentions_the_alternate_entry_points() -> None:
    """`veripen help <cmd>` 与 `-V` 都是真入口，总览里不该只让人去试（B262）。"""
    code, out = _run([])
    assert code == 0
    assert "veripen help chat" in out, out[-300:]
    assert "-V" in out, out[-300:]


def test_bare_veripen_still_works() -> None:
    """B261 那行 `args[0]` 没做空判断，裸跑 `veripen` 直接 IndexError（B262 抓到）。"""
    code, out = _run([])
    assert code == 0
    assert "用法：veripen" in out, out[:200]


# ── B301：退出码表要覆盖新规则（B300 先改行为，文档随后） ─────────
def test_readme_exit_table_covers_the_all_failed_rule() -> None:
    from pathlib import Path

    text = (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")
    row = next(ln for ln in text.splitlines() if ln.startswith("| `veripen run` |"))
    assert "0 项验证成功且有执行失败" in row, row
    assert "证据自校验失败" in row, row
