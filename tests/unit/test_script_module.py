"""B586 · `load_script_module()` 的行为——它存在的理由是**让断言顺序不再靠人记**。

同一段模块加载样板在 `tests/` 里出现 24 次，其中 3 处把
`assert spec is not None` 写在了 `module_from_spec(spec)` **之后**
（B580 / B582 / B585 各修一遍）。

三次单独修是不对的——**应该让下一次复制不到错的样板**。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "unit"))

from script_module import load_script_module  # noqa: E402


# ── 1：能加载真实的脚本 ─────────────────────────────────────
def test_loads_a_real_script() -> None:
    mod: Any = load_script_module("_probe_demo", ROOT / "scripts" / "demo.py")
    assert hasattr(mod, "main"), "演示脚本应该有 main()"


def test_accepts_str_path_too() -> None:
    """`Path` 与 `str` 都收——调用方两种写法都常见。"""
    mod: Any = load_script_module("_probe_demo_str", str(ROOT / "scripts" / "demo.py"))
    assert hasattr(mod, "main")


# ── 2：路径不存在时**抛 ImportError 而不是 AttributeError** ──
def test_missing_file_raises_import_error() -> None:
    """这是关键：`spec_from_file_location` 对不存在的文件返回 `None`。

    如果调用方把 `assert` 写在后面，会得到 `AttributeError: 'NoneType'...` ——
    那是个**误导性的错误**（看起来像模块内部的问题）。
    统一抛 `ImportError` 带路径，一眼看出是加载失败。
    """
    with pytest.raises(ImportError, match="加载不了脚本模块"):
        load_script_module("_nope", ROOT / "scripts" / "no_such_script.py")


# ── 3：同一文件可重复加载（换名字）──────────────────────────
def test_same_file_can_be_loaded_twice_with_different_names() -> None:
    """同一文件加载两次要用不同名字，否则拿到缓存。

    这个约束写进 docstring；这里验它**确实能用不同名字加载两次**。
    """
    a: Any = load_script_module("_probe_twice_a", ROOT / "scripts" / "demo.py")
    b: Any = load_script_module("_probe_twice_b", ROOT / "scripts" / "demo.py")
    assert a is not b, "两次加载应得到两个模块对象（名字不同）"
    assert hasattr(a, "main") and hasattr(b, "main")


# ── 4：返回类型是 Any（调用方不必再窄化）────────────────────
def test_returns_any_so_private_members_are_reachable() -> None:
    """被加载的脚本没有类型存根，返回 `ModuleType` 会让每个私有成员访问都报错。"""
    mod: Any = load_script_module("_probe_priv", ROOT / "scripts" / "demo.py")
    # 随便取一个模块级名字，不因"没有类型信息"而失败
    names = [n for n in dir(mod) if not n.startswith("__")]
    assert names, "模块里应该有可访问的名字"
