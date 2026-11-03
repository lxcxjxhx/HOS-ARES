#!/usr/bin/env python3
"""对比度闸门（B41）：令牌必须可读，而且**两套配色都要**。

只读 token 定义，不解析 CSS 规则 —— 因为规则只允许 `var(--token)`，
所以"文字色 vs 背景色"的对比度就是令牌之间的对比度。

用法：
    python scripts/ui/contrast_check.py           # 两套配色都查，不合格 exit 1
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "web" / "static" / "index.html"

#: (前景, 背景, 下限)。正文与小字按 4.5:1（WCAG AA）。
PAIRS = (
    ("fg", "bg", 4.5),
    ("fg", "surface", 4.5),
    ("muted", "bg", 4.5),
    ("muted", "surface", 4.5),
    ("weak", "bg", 4.5),
    ("danger", "danger-bg", 4.5),
    ("accent", "bg", 4.5),
)

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_ROOT = re.compile(r":root\s*\{([^}]*)\}")


def _rgb(value: str) -> tuple[int, int, int]:
    v = value.strip()
    if not _HEX.match(v):
        raise ValueError(f"不是十六进制色值: {value!r}")
    body = v[1:]
    if len(body) == 3:
        body = "".join(ch * 2 for ch in body)
    return int(body[0:2], 16), int(body[2:4], 16), int(body[4:6], 16)


def _channel(c: int) -> float:
    s = c / 255
    return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4


def luminance(hex_color: str) -> float:
    r, g, b = _rgb(hex_color)
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast(fg: str, bg: str) -> float:
    a, b = luminance(fg), luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def parse_tokens(text: str | None = None) -> list[dict[str, str]]:
    """返回两套令牌（浅色、深色）。缺哪套就少一个元素，由调用方报告。"""
    src = PAGE.read_text(encoding="utf-8") if text is None else text
    palettes: list[dict[str, str]] = []
    for block in _ROOT.findall(src):
        tokens = {
            name: value.strip()
            for name, value in re.findall(r"--([a-z0-9-]+)\s*:\s*([^;]+);", block)
            if _HEX.match(value.strip())
        }
        if "bg" in tokens and "fg" in tokens:
            palettes.append(tokens)
    return palettes


def check(palettes: list[dict[str, str]]) -> list[str]:
    problems: list[str] = []
    for index, tokens in enumerate(palettes):
        label = "light" if index == 0 else "dark" if index == 1 else f"palette{index}"
        for fg, bg, floor in PAIRS:
            if fg not in tokens or bg not in tokens:
                problems.append(f"{label}: 缺令牌 --{fg} 或 --{bg}")
                continue
            ratio = contrast(tokens[fg], tokens[bg])
            if ratio < floor:
                problems.append(f"{label}: {fg} on {bg} = {ratio:.2f} < {floor}")
    return problems


def main() -> int:
    palettes = parse_tokens()
    if len(palettes) < 2:
        print(f"contrast: FAIL: 只找到 {len(palettes)} 套配色（浅色 + 深色都要）")
        return 1
    problems = check(palettes)
    if problems:
        print(f"contrast: FAIL: {len(problems)} 处")
        for p in problems:
            print(" -", p)
        return 1
    print(f"contrast: OK: {len(palettes)} 套配色 × {len(PAIRS)} 对令牌")
    return 0


if __name__ == "__main__":
    sys.exit(main())
