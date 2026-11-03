"""验收点必须**能红**：``compare_transport`` 的 diff 不能恒为 0。

为什么单独一条测试管这件事：

``compare_transport.py`` 曾在 ``run_bench`` 切到真网络之后**自己和自己比**——
两侧都走真网络，于是 ``diff_count`` 恒为 0。一个永远绿的验收点，
比没有验收点更糟：它会让"传输替换等价"这句话看起来有证据，其实没有。

所以这里不问"diff 是不是 0"，而问"改了东西之后能不能变红"。
用最小侵入的方式制造一处真实差异：只换掉进程内一侧的目标文件符号。
若 diff 仍为 0，说明两侧共享了某个不该共享的东西——那才是要查的问题。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "b12" / "compare_transport.py"


def _load() -> ModuleType:
    for extra in (ROOT, ROOT / "tests", ROOT / "tests" / "integration"):
        if str(extra) not in sys.path:
            sys.path.insert(0, str(extra))
    spec = importlib.util.spec_from_file_location("_cmp_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _diff_count(mod: ModuleType) -> int:
    from veripen.bench import load_cases

    cases = load_cases(ROOT / "tests" / "bench" / "cases.yaml")
    return sum(
        1 for c in cases if mod._in_process(c) != mod._on_network(c)
    )


def test_comparison_is_zero_on_the_real_case_set() -> None:
    """基线：不改任何东西时，44 例应当两侧一致。"""
    mod = _load()
    assert _diff_count(mod) == 0


def test_comparison_goes_red_when_one_side_is_perturbed() -> None:
    """**关键**：只改进程内一侧的符号值，diff 必须非 0。

    改 ``SYSTEM_FILE`` 会同时影响 P-11/P-12（path_traversal 正样本）：
    进程内一侧找不到系统文件指纹 → REFUTED，而真网络一侧仍 VERIFIED。

    若这条测试失败（diff 仍是 0），意味着：两侧的符号、绑定或 transport
    有某一处被共享了，对比失去意义——那正是它要抓的东西。
    """
    mod = _load()
    pristine = dict(mod.SYMBOLS)
    mod.SYMBOLS = dict(pristine, SYSTEM_FILE="win.ini")
    try:
        pert = _diff_count(mod)
    finally:
        mod.SYMBOLS = pristine

    assert pert > 0, (
        "只改一侧输入，diff 仍为 0 —— 两侧共享了不该共享的东西，"
        "compare_transport 的 0 不再能证明任何事"
    )
    # 复原后必须回到 0：证明上面那次扰动没有留下状态
    assert _diff_count(mod) == 0


@pytest.mark.parametrize("side", ["in_process", "on_network"])
def test_both_sides_really_run_a_case(side: str) -> None:
    """两侧都必须真的跑出结论——不能一侧恒返回 BLOCKED 冒充"一致"。"""
    mod = _load()
    from veripen.bench import load_cases

    case = next(c for c in load_cases(ROOT / "tests" / "bench" / "cases.yaml")
                if c.case_id == "P-01")
    fn = mod._in_process if side == "in_process" else mod._on_network
    outcome, verdict = fn(case)
    assert outcome == "reported", f"{side} 没跑成：{outcome}/{verdict}"
    assert verdict == "VERIFIED", f"{side} 判定异常：{verdict}"
