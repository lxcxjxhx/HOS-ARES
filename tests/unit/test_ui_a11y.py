"""B42 可访问性判据（H07 B42 / H08 A16 的 a11y 部分）。

静态闸门只保证"骨架与属性"，所以这里的每个断言都对应一类**真实失效**：
没有地标 → 读屏无法跳读；没有 aria-live → 结果变化静默；正 tabindex → Tab 乱跳；
内联事件 → CSP 失效且无法审计。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ui" / "a11y_check.py"
PAGE = ROOT / "web" / "static" / "index.html"
CHECKLIST = ROOT / "docs" / "delivery" / "ui-a11y-checklist.md"
SRC = PAGE.read_text(encoding="utf-8")


def _load():
    spec = importlib.util.spec_from_file_location("a11y_check", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


a11y = _load()


# ── 通过态 ─────────────────────────────────────────────────────
def test_repo_passes_a11y_gate() -> None:
    assert a11y.checks() == []


def test_skip_link_and_focusable_main() -> None:
    assert 'class="skip" href="#main"' in SRC
    assert 'id="main" tabindex="-1"' in SRC


# ── 反例：每一类失效都要能红 ───────────────────────────────────
@pytest.mark.parametrize(
    ("mutate", "needle"),
    [
        (lambda s: s.replace('<html lang="zh-CN">', "<html>"), "lang"),
        (lambda s: s.replace('aria-live="polite"', ""), "aria-live"),
        (lambda s: s.replace('<nav id="nav" aria-label="页面">', '<nav id="nav">'), "aria-label"),
        (lambda s: s.replace('href="#main"', 'href="#top"'), "跳转链接"),
        (lambda s: s.replace('tabindex="-1"', ""), "不可聚焦"),
        (lambda s: s.replace("<body>", '<body><div onclick="x()">'), "内联事件"),
        (lambda s: s.replace('id: "q",', "id: \"q\", tabindex: \"3\","), "tabindex"),
        (lambda s: s.replace('"aria-label": "搜索发现", ', ""), "aria-label"),
        (lambda s: s.replace(":focus-visible", ":hover"), "focus-visible"),
        (lambda s: s.replace("prefers-reduced-motion", "prefers-nothing"), "reduced-motion"),
        (lambda s: s.replace("@media print", "@media screen"), "print"),
    ],
)
def test_mutations_are_caught(mutate, needle: str) -> None:  # noqa: ANN001
    problems = a11y.checks(mutate(SRC))
    assert any(needle in p for p in problems), f"{needle} 未被抓到：{problems}"


# ── 人工清单：必须存在、足量、且诚实 ───────────────────────────
def test_manual_checklist_exists_with_twelve_items() -> None:
    text = CHECKLIST.read_text(encoding="utf-8")
    rows = [ln for ln in text.splitlines() if ln.startswith("| ") and ln.count("|") >= 5]
    assert len(rows) >= 12, f"清单项不足 12：{len(rows)}"
    assert "没验" in text, "必须写清本次没验什么（D3）"
    assert "2026-11-03" in text, "验收记录必须带日期（时间节点制度）"


def test_checklist_records_unverified_items() -> None:
    text = CHECKLIST.read_text(encoding="utf-8")
    assert "7 8 9 10 11 12" in text, "未验项要逐条列出，不能只写「部分未验」"
