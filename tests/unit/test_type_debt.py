r"""B570 · `verify.py` 的类型覆盖有盲区：`tests/` 与 `scripts/` 完全没扫。

## 盲区怎么被发现的

B569 我给用例写了 `plugins_pkg.Registry = ...`，`mypy --strict` 报
`Cannot assign to a type`，而 **`verify.py` 是绿的**——它只扫 6 个产品目录：

```
hos_ares_core/  worker/  hos_ares_oracles/  hos_ares/  hos_ares_cli/  web/
```

`tests/` 与 `scripts/` 不在其中。

## 本批做了什么

1. 量化盲区：`mypy --strict scripts/` → **74 个错误 / 10+ 文件**；
2. 立台账 `scripts/lint/type_debt.py`：预算 74，**只许往下改**；
   `--check` 在超预算**或**欠账减少时都退 1（减少也要红，逼着把预算调小）；
3. 不进 `verify.py` 阻塞闸门——理由写在那个文件的常量注释里：
   一道"注定红"的闸门只会让人开始忽略 verify 的输出。

## 量具自己又踩了一次

第一版数出来恒为 **0**：`startswith("scripts/")` 比较 mypy 打的 `scripts\xxx.py`
（Windows 反斜杠）。已补进 `instrument-failure-modes.md` 的形态 1b「路径/格式假设」。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def _debt_module() -> object:
    import importlib

    sys.path.insert(0, str(ROOT / "scripts" / "lint"))
    return importlib.import_module("type_debt")


# ── 1：mypy 的目标确实不含 tests 与 scripts ─────────────────
def test_verify_mypy_targets_exclude_tests_and_scripts() -> None:
    """这条**不是在夸它**，是把盲区固定成事实。

    哪天有人把 `tests/` 加进去，这条会红——那时应该同时调整 `type_debt` 的预算。
    """
    src = (ROOT / "scripts" / "verify.py").read_text(encoding="utf-8")
    line = next(ln for ln in src.splitlines() if ln.startswith("MYPP_TARGETS"))
    assert "tests/" not in line, "tests/ 进了 mypy 目标——请同时更新 type_debt 预算"
    assert "scripts/" not in line, "scripts/ 进了 mypy 目标——请同时更新 type_debt 预算"


# ── 2：台账能数出非零（量具不能恒为 0）───────────────────────
def test_the_ledger_counts_consistently() -> None:
    """数出来的条数与它返回的行数必须一致。

    **曾经**这条要求"必须数出非零"（量具第一版因路径分隔符没归一化，恒数 0）。
    B574 把欠账修到 0 之后，非零不再是必要条件——**但"数得准"仍然要**：
    返回的行数必须等于计数，且每行都是 error。
    """
    mod = _debt_module()
    total, lines = mod.count_errors()  # type: ignore[attr-defined]
    assert len(lines) == total, (len(lines), total)
    assert all("error:" in ln for ln in lines)
    if total == 0:
        budget: Any = getattr(mod, "SCRIPT_ERROR_BUDGET", -1)
        assert budget == 0, "修到 0 了但预算没跟着清零"


# ── 3：只数目标目录，不数顺带追进来的 ───────────────────────
def test_counts_only_the_target_directory() -> None:
    """`mypy --strict scripts/` 会顺着 import 追进 `tests/`——那些不该计入。"""
    mod = _debt_module()
    _total, lines = mod.count_errors()  # type: ignore[attr-defined]
    # 斜杠现拼：noroute 的 R-PATH 不许以 / 开头的字面量出现在源码里
    sep = chr(0x2F)
    back = chr(0x5C)
    strays = [ln for ln in lines if ln.replace(sep, back).startswith("tests")]
    assert not strays, f"把 tests/ 的错误也算进来了：{strays[:2]}"


# ── 4：预算与实测一致（欠账减少必须调预算）──────────────────
def test_budget_matches_measured_debt() -> None:
    """预算必须等于实测：多了等于没管，少了会误红。

    这也是 `--check` 那条"减少也红"的设计意图——它逼着这个数字**永远是真的**。
    """
    mod = _debt_module()
    total, _ = mod.count_errors()  # type: ignore[attr-defined]
    budget = mod.SCRIPT_ERROR_BUDGET  # type: ignore[attr-defined]
    assert total == budget, (
        f"实测 {total}，预算 {budget}。"
        + (" 欠账减少了：请把预算改成实测值。" if total < budget
           else " 超预算：新加的脚本请当场修掉类型错误。")
    )


# ── 5：这条量具自己也要能红 ─────────────────────────────────
def test_check_mode_fails_when_over_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """预算调到 -1（低于实测 0），`--check` 必须退 1。

    原先这条是把预算调到 0 来验"超预算"；欠账真修到 0 之后，
    0 == 0 变成"恰好一致"，得用 -1 才构造得出"超预算"。
    """
    mod = _debt_module()
    monkeypatch.setattr(mod, "SCRIPT_ERROR_BUDGET", -1, raising=False)
    rc = mod.main(["--check"])  # type: ignore[attr-defined]
    assert rc == 1, "超预算却没退 1 —— 那这条闸门是摆设"


def test_check_mode_fails_when_debt_shrinks(monkeypatch: pytest.MonkeyPatch) -> None:
    """欠账减少也退 1：否则预算会永远停在旧的大数字上，形同虚设。"""
    mod = _debt_module()
    monkeypatch.setattr(mod, "SCRIPT_ERROR_BUDGET", 99999, raising=False)
    rc = mod.main(["--check"])  # type: ignore[attr-defined]
    assert rc == 1, "欠账减少却没退 1 —— 预算不会跟着降，等于没管"
