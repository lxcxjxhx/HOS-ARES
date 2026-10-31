#!/usr/bin/env python3
"""lint-nodata-stub —— 禁用 mock/stub/fake/dummy 与硬编码数据（26 §5.1）。

四查：
 1. 产品代码/配置中出现 mock|stub|fake|dummy（命中即失败）
 2. .py 中 >=5 个常量元素的字面量列表/元组（疑似硬编码行集）
 3. data/ 下每个目录必须有 manifest.json（本批 data/ 不存在 → 跳过并说明）
 4. 未固定种子的 random.choice/randint/random()
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = ("veripen_core", "veripen_oracles", "veripen_contract", "api", "worker", "web", "tests")
CFG_EXT = {".py", ".yaml", ".yml", ".json"}
BANNED = re.compile(r"\b(mock|stub|fake|dummy)\b", re.IGNORECASE)
RANDOM = re.compile(r"random\.(choice|randint|random)\(")
MIN_ROWS = 5


def _files() -> list[Path]:
    out: list[Path] = []
    for d in SCAN_DIRS:
        base = ROOT / d
        if base.exists():
            out += [p for p in base.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    return sorted(out)


def _check_banned(files: list[Path]) -> list[str]:
    problems: list[str] = []
    me = Path(__file__).resolve()
    for p in files:
        if p.suffix not in CFG_EXT or p.resolve() == me:
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if BANNED.search(line):
                problems.append(f"{p.relative_to(ROOT).as_posix()}:{i}: 禁用词: {line.strip()[:100]}")
    return problems


def _check_hardcoded(files: list[Path]) -> list[str]:
    problems: list[str] = []
    for p in files:
        if p.suffix != ".py":
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        except SyntaxError:
            continue
        # __all__ 是导出清单，不是数据行集：跳过
        skip: set[int] = set()
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets = [node.target]
            if any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets):
                skip.add(id(node.value))
        for node in ast.walk(tree):
            if isinstance(node, (ast.List, ast.Tuple)) and id(node) not in skip:
                consts = [e for e in node.elts if isinstance(e, ast.Constant)]
                if len(consts) >= MIN_ROWS and len(consts) == len(node.elts):
                    problems.append(
                        f"{p.relative_to(ROOT).as_posix()}:{node.lineno}: {len(consts)} 行常量字面量"
                    )
    return problems


def _check_data_manifest() -> list[str]:
    data = ROOT / "data"
    if not data.exists():
        return []
    problems: list[str] = []
    for sub in sorted(x for x in data.iterdir() if x.is_dir()):
        if not (sub / "manifest.json").exists():
            problems.append(f"{sub.relative_to(ROOT).as_posix()}: 缺 manifest.json")
    return problems


def _check_random(files: list[Path]) -> list[str]:
    problems: list[str] = []
    for p in files:
        if p.suffix != ".py":
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if RANDOM.search(line) and "seed(" not in line:
                problems.append(f"{p.relative_to(ROOT).as_posix()}:{i}: 未固定种子的随机: {line.strip()[:100]}")
    return problems


def main() -> int:
    files = _files()
    problems = (
        _check_banned(files)
        + _check_hardcoded(files)
        + _check_data_manifest()
        + _check_random(files)
    )
    if problems:
        print(f"lint-nodata-stub: FAIL: {len(problems)} 命中")
        for x in problems:
            print("  " + x)
        return 1
    note = "" if (ROOT / "data").exists() else "（data/ 尚未建立，第 3 查跳过）"
    print(f"lint-nodata-stub: OK: 0 命中（扫描 {len(files)} 文件）{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
