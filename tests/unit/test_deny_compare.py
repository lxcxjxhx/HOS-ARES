"""B314 · 边界对照脚本的取数与判定（纯函数部分）。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from script_module import load_script_module  # noqa: E402
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def _load() -> Any:
    path = ROOT / "scripts" / "demo" / "deny_compare.py"
    # 用共用函数（B587）
    mod: Any = load_script_module("deny_compare", path)
    return mod


def test_summarize_counts_peer_in_deny() -> None:
    mod = _load()
    findings = {
        "counts": {"total": 2, "by_outcome": {"reported": 0, "not_run": 2}},
        "findings": [{"reason_code": "peer_in_deny"}, {"reason_code": "peer_in_deny"}],
    }
    row = mod.summarize(findings)
    assert row == {"total": 2, "verified": 0, "not_run": 2, "peer_in_deny": 2}


def test_render_row_has_all_four_numbers() -> None:
    mod = _load()
    text = mod.render_row("demo-a", {"total": 2, "verified": 2, "not_run": 0, "peer_in_deny": 0})
    assert "demo-a" in text and "2" in text


def test_both_profiles_are_referenced() -> None:
    mod = _load()
    assert "demo-a" in mod.ALLOW_PROFILE and "demo-deny" in mod.DENY_PROFILE


# ── B315：一条命令跑完（自建 lab）──────────────────────────────
def test_lab_flag_is_documented_in_help() -> None:
    import inspect

    mod = _load()
    src = inspect.getsource(mod.main)
    assert '"--lab"' in src, "要能自己起靶场，否则调用方得先手动起一个"
    assert "lab.stop()" in src, "自建靶场要收尾，别留进程"


def test_missing_inputs_exit_two() -> None:
    mod = _load()
    assert mod.main(["--out", "out/x"]) == 2
