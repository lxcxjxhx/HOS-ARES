#!/usr/bin/env python3
"""dead-exports —— 找出"实现了但没有生产调用方"的公开符号。

这轮欠账的根因：`injectable()` / `with_knowledge()` / `compress()` 三个模块
单测全绿、`check-plan` 全绿，但生产路径一次都没调过。测试只证明"函数行为对"，
不证明"被用上了"。

本检查器回答一个问题：**这个公开符号，除了定义处和 tests/，还有谁在用？**

判定口径：
- 生产侧 = `veripen_core/` + `veripen_cli/`（排除定义该符号的文件）
- 测试侧 = `tests/`
- 只在测试侧出现 ⇒ 接线欠账（`dead`）
- 两侧都不出现 ⇒ 死代码（`unused`）

误报边界（已知，不修）：动态引用（`getattr`、`__all__` 再导出、插件注册表
按名调用）会被算成"无人调用"。所以本工具**只报不判**，退出码恒 0；
要当门用请先看基线清单。

用法：
  python scripts/lint/dead_exports.py            # 人类可读
  python scripts/lint/dead_exports.py --json     # 机器可读
  python scripts/lint/dead_exports.py --baseline out/dead-exports.baseline.txt
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "veripen_core"
CLI = ROOT / "veripen_cli"
TESTS = ROOT / "tests"


def _py(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def _rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def collect_defs() -> dict[str, list[str]]:
    """core 公开符号 → 定义位置列表。

    同时收顶层函数/类**与类方法**：上一轮漏掉的就是方法——
    `AssetFacts.injectable()` / `Context.with_knowledge()` / 都在这条缝里，
    只扫顶层会给出"全部干净"的假结论。
    """
    out: dict[str, list[str]] = {}

    def add(name: str, where: str) -> None:
        if name.startswith("_"):
            return
        out.setdefault(name, []).append(where)

    for path in _py(CORE):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        rel = _rel(path)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                add(node.name, rel)
                if isinstance(node, ast.ClassDef):
                    for sub in node.body:
                        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            add(sub.name, f"{rel}#{node.name}")
    return out


def scan() -> list[dict[str, object]]:
    defs = collect_defs()
    prod: dict[str, str] = {}
    for path in _py(CORE) + _py(CLI):
        prod[_rel(path)] = path.read_text(encoding="utf-8", errors="ignore")
    test_blob = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in _py(TESTS))

    rows: list[dict[str, object]] = []
    for name, owners in defs.items():
        owner_files = {o.split("#")[0] for o in owners}
        pattern = re.compile(r"\b" + re.escape(name) + r"\b")
        callers = [
            {"file": f, "hits": len(pattern.findall(text))}
            for f, text in prod.items()
            if f not in owner_files and pattern.search(text)
        ]
        if callers:
            continue
        rows.append(
            {
                "symbol": name,
                "defined_in": owners,
                "test_refs": len(pattern.findall(test_blob)),
                "kind": "dead" if pattern.search(test_blob) else "unused",
            }
        )
    return sorted(rows, key=lambda r: (r["kind"] != "dead", -int(r["test_refs"]), r["symbol"]))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="找出无生产调用方的公开符号")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--baseline", default="", help="只列不在基线里的新增项")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    rows = scan()
    baseline: set[str] = set()
    if args.baseline:
        p = ROOT / args.baseline
        if p.exists():
            baseline = {
                line.strip().split("\t")[0]
                for line in p.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.startswith("#")
            }
    if baseline:
        rows = [r for r in rows if r["symbol"] not in baseline]

    if args.json:
        print(json.dumps({"count": len(rows), "rows": rows}, ensure_ascii=False, indent=2))
        return 0

    dead = [r for r in rows if r["kind"] == "dead"]
    unused = [r for r in rows if r["kind"] == "unused"]
    print(f"dead-exports: 接线欠账 {len(dead)} / 死代码 {len(unused)}\n")
    if dead:
        print("只在 tests/ 里被调用（模块对，但生产路径没接）：")
        for r in dead:
            print(f"  {r['symbol']:<34} tests={r['test_refs']:<3} {','.join(r['defined_in'])}")
        print()
    if unused:
        print("两侧都没出现（可能死代码，也可能是动态引用）：")
        for r in unused:
            print(f"  {r['symbol']:<34} {','.join(r['defined_in'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
