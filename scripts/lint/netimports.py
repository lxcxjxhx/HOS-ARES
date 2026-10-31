#!/usr/bin/env python3
"""netimports —— 来源适配器 / 契约层不许直接引用网络库（27 §4.2 第三条铁律，L0-33/34）。

只做静态 import 扫描（AST），不导入模块、不发请求。

用法：
  python scripts/lint/netimports.py [--path DIR ...]
默认扫描 veripen_core/routes 与 veripen_contract（若存在）。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATHS = ("veripen_core/routes", "veripen_contract")
FORBIDDEN = ("httpx", "requests", "aiohttp", "urllib.request", "urllib3", "http.client")


def forbidden_imports_in(path: Path) -> list[str]:
    """返回 ``文件:行: 模块`` 形式的命中清单。"""
    hits: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        mods: list[str] = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module]
        for mod in mods:
            if any(mod == f or mod.startswith(f + ".") for f in FORBIDDEN):
                hits.append(f"{path}:{node.lineno}: {mod}")
    return hits


def scan(paths: list[Path]) -> list[str]:
    hits: list[str] = []
    for base in paths:
        if not base.exists():
            continue
        for p in sorted(base.rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            try:
                hits.extend(forbidden_imports_in(p))
            except SyntaxError as exc:  # 语法错误也要暴露，不静默跳过
                hits.append(f"{p}: SyntaxError: {exc}")
    return hits


def main(argv: list[str]) -> int:
    paths: list[Path] = []
    i = 0
    while i < len(argv):
        if argv[i] == "--path":
            paths.append(Path(argv[i + 1]))
            i += 2
        else:
            i += 1
    if not paths:
        paths = [ROOT / p for p in DEFAULT_PATHS]
    hits = scan(paths)
    if hits:
        print(f"netimports: FAIL: {len(hits)} 命中")
        for h in hits:
            print("  " + h)
        return 1
    print(f"netimports: OK: 0 命中（扫描 {len(paths)} 目录）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
