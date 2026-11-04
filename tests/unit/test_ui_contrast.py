"""B41 视觉系统判据（H07 B41 / H08 A16）。

两条机械判据：
1. **源码里不许出现裸色值**（除 `:root` 令牌块）——否则"改一次主题要全文件找色号"；
2. **两套配色的对比度都要过 4.5:1** —— 深色不是"顺手加个媒体查询"，它得能读。
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "web" / "static" / "index.html"
SCRIPT = ROOT / "scripts" / "ui" / "contrast_check.py"
SRC = PAGE.read_text(encoding="utf-8")
_TOKEN_USES = "var(--bg) var(--fg) var(--line) var(--surface) var(--muted)".split()


def _load():
    spec = importlib.util.spec_from_file_location("contrast_check", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cc = _load()


# ── 对比度算法本身 ─────────────────────────────────────────────
def test_contrast_extremes() -> None:
    assert cc.contrast("#000000", "#ffffff") == pytest.approx(21.0, rel=1e-3)
    assert cc.contrast("#123456", "#123456") == pytest.approx(1.0)


def test_repo_palettes_both_exist_and_pass() -> None:
    palettes = cc.parse_tokens()
    assert len(palettes) == 2, "浅色 + 深色两套令牌都要有"
    assert palettes[0]["bg"] != palettes[1]["bg"], "深色不能等于浅色"
    assert cc.check(palettes) == []


def test_checker_can_go_red() -> None:
    bad = [{"fg": "#777777", "bg": "#888888", "muted": "#777777", "surface": "#888888",
            "weak": "#777777", "danger": "#777777", "danger-bg": "#888888", "accent": "#777777"}]
    problems = cc.check(bad)
    assert problems, "灰底灰字必须被判红（否则这个门是摆设）"


def test_missing_pair_is_reported() -> None:
    problems = cc.check([{"fg": "#000000", "bg": "#ffffff"}])
    assert any("缺令牌" in p for p in problems)


# ── 源码纪律：裸色值只在令牌块里 ────────────────────────────────
def _style_blocks(text: str) -> tuple[str, str]:
    blocks = re.findall(r"<style>(.*?)</style>", text, re.S)
    rest = re.sub(r"<style>.*?</style>", "", text, flags=re.S)
    return "\n".join(blocks), rest


def test_no_hex_outside_style_block() -> None:
    _, rest = _style_blocks(SRC)
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", rest), "样式块之外出现裸色值"


def test_hex_only_inside_token_blocks() -> None:
    style, _ = _style_blocks(SRC)
    outside_tokens = re.sub(r":root\s*\{[^}]*\}", "", style)
    outside_tokens = re.sub(r"@media \(prefers-color-scheme: dark\)\s*\{\s*:root\s*\{[^}]*\}\s*\}",
                            "", outside_tokens)
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", outside_tokens), \
        "颜色必须走 var(--token)，不许在规则里写死色号"


def test_rules_use_tokens() -> None:
    style, _ = _style_blocks(SRC)
    for token in _TOKEN_USES:
        assert token in style, f"缺少令牌使用 {token}"


# ── 三态 + 密度 + 可访问性钩子 ────────────────────────────────
def test_three_states_present() -> None:
    assert "加载中…" in SRC and "aria-busy" in SRC
    assert "没有匹配的已确认结论" in SRC and 'role: "status"' in SRC
    assert "state-error" in SRC and 'role: "alert"' in SRC


def test_density_is_local_only() -> None:
    assert 'data-density' in SRC and 'localStorage' in SRC
    assert "veripen.density" in SRC
    assert "fetch(" in SRC and "density" not in SRC.split("fetch(")[1].split(")")[0], \
        "密度偏好不得进入任何请求"


def test_reduced_motion_and_print() -> None:
    assert "prefers-reduced-motion" in SRC
    assert "@media print" in SRC
