#!/usr/bin/env python3
"""可访问性静态闸门（B42）。

**它能做什么**：检查页面骨架与属性是否齐全（语义地标、可访问名称、焦点样式、动效与打印）。
**它不能做什么**：不验证真实焦点顺序、不验证屏幕阅读器朗读、不验证浏览器渲染——
这三件事需要浏览器，本仓库没有浏览器 CI，所以它们进人工清单（`docs/delivery/ui-a11y-checklist.md`），
PR 里必须写明"验了什么 / 没验什么"。

用法：
    python scripts/ui/a11y_check.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "web" / "static" / "index.html"


def checks(text: str | None = None) -> list[str]:
    """返回问题列表；空列表 = 通过。每条都写"为什么"。"""
    src = PAGE.read_text(encoding="utf-8") if text is None else text
    problems: list[str] = []

    if not re.search(r"<html[^>]+lang=", src):
        problems.append("缺 <html lang>：屏幕阅读器无法选择发音")
    if "<main" not in src or 'id="main"' not in src:
        problems.append("缺 <main id=main>：没有主内容地标")
    if 'aria-live' not in src:
        problems.append("缺 aria-live：动态结果变化不会被读出")
    if not re.search(r"<nav[^>]+aria-label=", src):
        problems.append("缺 <nav aria-label>：多个导航区无法区分")
    if 'href="#main"' not in src:
        problems.append("缺跳转链接：键盘用户要跳过导航必须 Tab 过整个菜单")
    if not re.search(r'<main[^>]+tabindex="-1"', src):
        problems.append("跳转目标不可聚焦：跳转后焦点仍在链接上")

    # 正 tabindex 会打乱自然顺序（HTML 属性与 el() 的 JS 属性两种写法都要抓）
    for m in re.finditer(r'tabindex\s*[=:]\s*"([1-9][0-9]*)"', src):
        problems.append(f"出现正 tabindex={m.group(1)}：会打乱 Tab 顺序")

    # 内联事件（CSP 与可审计性）
    if re.search(r"\son[a-z]+\s*=", src, re.I):
        problems.append("出现内联事件属性（on*=）：CSP 不允许，也无法集中审计")

    # 表单控件必须有可访问名称
    for tag in re.findall(r"<(?:input|select|textarea)\b[^>]*>", src):
        if not re.search(r'aria-label=|aria-labelledby=|<label', tag):
            problems.append(f"表单控件缺可访问名称：{tag[:60]}")
    for tag in re.findall(r'el\("(?:input|select|textarea)"\s*,\s*\{([^}]*)\}', src):
        if "aria-label" not in tag:
            problems.append(f"动态表单控件缺 aria-label：el(...) 属性 {tag[:60]}")

    # 焦点可见
    if ":focus-visible" not in src:
        problems.append("缺 :focus-visible：键盘用户看不到焦点在哪")
    if "--focus" not in src:
        problems.append("焦点色未令牌化：深色主题下会看不见")

    # 动效与打印
    if "prefers-reduced-motion" not in src:
        problems.append("缺 prefers-reduced-motion：前庭敏感用户无法关动效")
    if "@media print" not in src:
        problems.append("缺 @media print：打印会带出导航与工具条")

    # 表格：行可聚焦时要能说明身份
    if 'tabindex: "0"' in src and "data-id" not in src:
        problems.append("可聚焦行缺 data-id：焦点移动时无法说明当前是哪一条")

    return problems


def main() -> int:
    problems = checks()
    if problems:
        print(f"a11y: FAIL: {len(problems)} 处")
        for p in problems:
            print(" -", p)
        return 1
    print("a11y: OK: 地标/名称/焦点/动效/打印 静态检查通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
