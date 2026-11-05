"""B82 · 统一 CLI 入口（H24 判据 A–E）。

只测分发与透传，不测各子命令自身行为（那是它们的批次）。
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout

import pytest

from veripen_cli.__main__ import COMMANDS, main


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out):
        rc = main(argv)
    return rc, out.getvalue(), err.getvalue()


# ── C：--help 列全 ─────────────────────────────────────────────
def test_help_lists_every_command() -> None:
    rc, out, _ = _run(["--help"])
    assert rc == 0
    for name, _, _ in COMMANDS:
        assert name in out, f"{name} 没在帮助里"
    assert "veripen <子命令>" in out


def test_every_command_has_description() -> None:
    for name, module, desc in COMMANDS:
        assert desc.strip(), f"{name} 缺一句话说明"
        assert module.startswith("veripen_cli."), f"{name} 的模块路径不对"


# ── B：未知子命令 ─────────────────────────────────────────────
def test_unknown_command_exits_two(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["nosuch"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "未知子命令" in err


# ── A：子命令可调起（用最轻的两个验）──────────────────────────
def test_doctor_subcommand_runs(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["doctor", "--only", "python"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "python" in out


def test_parity_help_passthrough(capsys: pytest.CaptureFixture[str]) -> None:  # noqa: ANN001
    """透传：子命令的 --help 由它自己处理，中间层不解析。"""
    with pytest.raises(SystemExit) as exc:
        main(["run", "--help"])
    assert exc.value.code == 0


# ── D：直调路径不变 ───────────────────────────────────────────
def test_direct_module_path_still_works() -> None:
    import importlib

    mod = importlib.import_module("veripen_cli.cmd_doctor")
    assert callable(getattr(mod, "main", None)), "直调入口必须仍然存在"


# ── 版本号与 pyproject 一致 ───────────────────────────────────
def test_version_matches_pyproject() -> None:
    from pathlib import Path

    import veripen_cli

    root = Path(__file__).resolve().parents[2]
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{veripen_cli.__version__}"' in text, "版本号与 pyproject 不一致"
    assert 'veripen = "veripen_cli.__main__:main"' in text, "缺入口脚本声明"
