#!/usr/bin/env python3
"""lint-act-cmd —— 35 §5：``actions/`` 下的 argv 模板不许出现字面地址。

三查：
1. `ARGV_TEMPLATES` 的每个模板项里出现 `://`、IP、`host:port` → 红；
2. `actions/` 源码里出现 ``"http://..."``/``"https://..."`` 这类字面 URL → 红；
3. `actions/` 下 import ``Verdict`` / ``Judgement`` → 红（动作层不许判定）。

用法：python scripts/lint/act_cmd.py
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACTIONS_DIR = ROOT / "veripen_core" / "actions"

#: 地址指纹：scheme、IPv4、显式 host:port
ADDRESS = re.compile(
    r"[a-zA-Z][a-zA-Z0-9+.\-]*://"
    r"|\b[0-9]{1,3}(\.[0-9]{1,3}){3}\b"
    r"|\b(localhost|0\.0\.0\.0)\b"
    r"|(?<=[a-zA-Z]):[0-9]{2,5}(?![0-9])"
)
#: 判定类型：动作层出现即越界
BANNED_NAMES = frozenset({"Verdict", "Judgement"})


def _py_files() -> list[Path]:
    if not ACTIONS_DIR.exists():
        return []
    return sorted(p for p in ACTIONS_DIR.rglob("*.py") if "__pycache__" not in p.parts)


def _check_templates() -> list[str]:
    sys.path.insert(0, str(ROOT))
    from veripen_core.actions import ARGV_TEMPLATES

    problems: list[str] = []
    for binary, template in ARGV_TEMPLATES.items():
        for part in template:
            if ADDRESS.search(part):
                problems.append(f"{binary}: 模板项 {part!r} 含字面地址")
    return problems


def _check_source() -> list[str]:
    problems: list[str] = []
    for p in _py_files():
        rel = p.relative_to(ROOT).as_posix()
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if ADDRESS.search(line):
                problems.append(f"{rel}:{i}: 字面地址 {line.strip()[:80]}")
    return problems


def _check_no_verdict() -> list[str]:
    """AST 扫 import：动作层引用判定类型即红（35 §8 第 2 条）。"""
    problems: list[str] = []
    for p in _py_files():
        rel = p.relative_to(ROOT).as_posix()
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.ImportFrom):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.Import):
                names = [a.name.split(".")[-1] for a in node.names]
            for n in names:
                if n in BANNED_NAMES:
                    problems.append(f"{rel}:{node.lineno}: 动作层 import 判定类型 {n}")
    return problems


def main() -> int:
    files = _py_files()
    problems = _check_templates() + _check_source() + _check_no_verdict()
    if problems:
        print(f"lint-act-cmd: FAIL: {len(problems)} 命中")
        for x in problems:
            print("  " + x)
        return 1
    print(f"lint-act-cmd: OK: 0 命中（扫描 {len(files)} 文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
