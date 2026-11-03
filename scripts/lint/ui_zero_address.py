#!/usr/bin/env python3
"""lint-ui —— 34 §1：操作台页面**零地址**。

两查：
1. ``web/`` 下的页面与脚本里出现模板正文 / host / IP / ``:<port>`` → 红；
   只允许 ``route_ref`` 形态（``route://`` 与占位符）。
2. 页面里出现剪贴板 API 或可选文本样式 → 红（34 §3：不能复制地址）。

用法：python scripts/lint/ui_zero_address.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = ROOT / "web"

#: 任意 scheme、IPv4、环回、host:port
ADDRESS = re.compile(
    r"[a-zA-Z][a-zA-Z0-9+.\-]*://"
    r"|\b[0-9]{1,3}(\.[0-9]{1,3}){3}\b"
    r"|\b(localhost|0\.0\.0\.0)\b"
    r"|(?<=[a-zA-Z]):[0-9]{2,5}(?![0-9])"
)
#: 允许出现的 scheme（我们自己的不透明引用 + SVG/XML 命名空间）
ALLOWED = ("route://", "vault://", "op://", "http://www.w3.org/", "https://www.w3.org/")

#: 复制地址相关的 API（34 §3 末行：user-select:none + 无剪贴板 API）
CLIPBOARD = re.compile(r"navigator\.clipboard|document\.execCommand\s*\(\s*['\"]copy")
SELECTABLE = re.compile(r"user-select\s*:\s*(text|auto)", re.IGNORECASE)

CODE_EXT = {".py", ".html", ".js", ".mjs", ".css", ".json"}


def _files() -> list[Path]:
    if not WEB_DIR.exists():
        return []
    return sorted(
        p for p in WEB_DIR.rglob("*")
        if p.is_file() and p.suffix in CODE_EXT and "__pycache__" not in p.parts
    )


def main() -> int:
    problems: list[str] = []
    files = _files()
    for p in files:
        rel = p.relative_to(ROOT).as_posix()
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            scrubbed = line
            for tok in sorted(ALLOWED, key=len, reverse=True):
                scrubbed = scrubbed.replace(tok, "<ok>")
            m = ADDRESS.search(scrubbed)
            if m:
                problems.append(f"{rel}:{i}: 地址 {m.group(0)!r}")
            if CLIPBOARD.search(line):
                problems.append(f"{rel}:{i}: 出现剪贴板 API（不能复制地址）")
            if SELECTABLE.search(line):
                problems.append(f"{rel}:{i}: 出现可选文本样式（不能复制地址）")

    if problems:
        print(f"lint-ui: FAIL: {len(problems)} 命中")
        for x in problems:
            print("  " + x)
        return 1
    print(f"lint-ui: OK: 0 命中（扫描 {len(files)} 文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
