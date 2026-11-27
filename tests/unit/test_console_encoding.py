"""B574 · `scripts/` 类型欠账清零（19 → 0），并把重复 11 次的 `reconfigure` 收口。

## 为什么最后一步是抽共用工具

`sys.stdout.reconfigure(encoding="utf-8", errors="replace")` 在 **11 个脚本**里各写了一遍，
每处都带同一个类型问题（`TextIO` 没有这个方法）。逐个加 `getattr` 是 11 份同样的代码；
抽一个 `scripts/lint/console.py` 之后，11 处都变成一行 `utf8_stdout()`。

## 本批钉住什么

| 判据 | 为什么 |
|---|---|
| `console.utf8_stdout()` 不抛 | 它是所有脚本的入口 |
| 流**没有** `reconfigure` 时静默跳过 | 重定向到管道 / StringIO 时就是这样，不能因此崩 |
| `reconfigure` 自己抛（已写完一行）时也不崩 | `TextIOWrapper` 的硬约束 |
| 欠账预算是 0 | 清零之后不许再涨 |
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _console() -> object:
    import importlib

    sys.path.insert(0, str(ROOT / "scripts" / "lint"))
    return importlib.import_module("console")


# ── 1：正常流上不抛 ─────────────────────────────────────────
def test_utf8_stdout_does_not_raise() -> None:
    mod = _console()
    mod.utf8_stdout()  # type: ignore[attr-defined]
    mod.utf8_stdout(encoding="utf-8", errors="strict")  # type: ignore[attr-defined]


# ── 2：流没有 reconfigure 时静默跳过 ────────────────────────
def test_stream_without_reconfigure_is_skipped() -> None:
    """重定向到 `StringIO` / 管道时没有 `reconfigure`——不许因此崩。"""

    class _Bare:
        def write(self, _s: str) -> int:
            return 0

    mod = _console()
    mod.utf8_stream(_Bare())  # type: ignore[attr-defined]  # 不抛即通过


# ── 3：reconfigure 自己抛时也不崩 ───────────────────────────
def test_stream_whose_reconfigure_raises_is_tolerated() -> None:
    """`TextIOWrapper` 在**已经写过一行**之后不允许再改编码。

    那种情况下保持原样，不因为"编码没改成"让整个脚本失败。
    """

    class _Angry:
        def reconfigure(self, **_kw: object) -> None:
            raise ValueError("can't reconfigure after first write")

    mod = _console()
    mod.utf8_stream(_Angry())  # type: ignore[attr-defined]  # 不抛即通过


# ── 4：欠账必须为 0 ────────────────────────────────────────
def test_script_type_debt_is_zero() -> None:
    """清零之后不许再涨：新加脚本带类型错误会被这条挡住。"""
    import importlib

    sys.path.insert(0, str(ROOT / "scripts" / "lint"))
    mod = importlib.import_module("type_debt")
    total, lines = mod.count_errors()
    assert total == 0, f"scripts/ 又有 {total} 个类型错误：{lines[:3]}"
    assert mod.SCRIPT_ERROR_BUDGET == 0


# ── 5：内联写法只许剩下"已经自己包好 try/except"的那几处 ──────
def test_inline_reconfigure_is_only_the_guarded_ones() -> None:
    """内联 `reconfigure` 只允许出现在**已经自己处理了异常**的地方。

    为什么不一刀切到共用工具：`scripts/verify.py` / `scripts/demo.py` /
    `scripts/audit_model_conformance.py` 那三处本来就写成

    ```python
    try:
        stream.reconfigure(...)
    except (AttributeError, ValueError):
        pass
    ```

    ——语义与 `console.utf8_stdout()` 完全一致，只是早写了一步。
    强行统一只会制造无意义的 diff。真正该禁的是**没保护的裸调用**：
    那种在重定向 / 老 Python 上会直接崩。
    """
    import re

    offenders: list[str] = []
    for path in (ROOT / "scripts").rglob("*.py"):
        if path.name == "console.py":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"^\s*\S+\.reconfigure\(", text, re.M):
            # 往上找 6 行内有没有 try:
            head = text[: m.start()].splitlines()[-6:]
            guarded = any("try:" in ln for ln in head)
            if not guarded:
                rel = str(path.relative_to(ROOT))
                offenders.append(f"{rel}: 裸调用 reconfigure")
    assert not offenders, f"未保护的 reconfigure：{offenders}"
