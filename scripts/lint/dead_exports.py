#!/usr/bin/env python3
"""dead-exports —— 找出"实现了但没有生产调用方"的公开符号。

这轮欠账的根因：`injectable()` / `with_knowledge()` / `compress()` 三个模块
单测全绿、`check-plan` 全绿，但生产路径一次都没调过。测试只证明"函数行为对"，
不证明"被用上了"。

本检查器回答一个问题：**这个公开符号，除了定义处和 tests/，还有谁在用？**

判定口径：
- 生产侧 = `hos_ares_core/` + `hos_ares_cli/`（排除定义该符号的文件）
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

from typing import Any

import argparse
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "hos_ares_core"
CLI = ROOT / "hos_ares_cli"
TESTS = ROOT / "tests"


def _py(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


#: 框架会回调的方法名前缀（HTMLParser / socketserver / 事件源）
CALLBACK_PREFIXES = ("handle_", "parse_", "do_", "on_")


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
        # 框架回调不算死代码（B437）：`HTMLParser` 的 handle_*、`BaseHTTPRequestHandler`
        # 的 do_*、事件源的 on_* —— 调用方是基类或运行时，文本里搜不到名字是正常的。
        if any("#" in o for o in owners) and name.startswith(CALLBACK_PREFIXES):
            continue
        owner_files = {o.split("#")[0] for o in owners}
        pattern = re.compile(r"\b" + re.escape(name) + r"\b")
        callers = [
            {"file": f, "hits": len(pattern.findall(text))}
            for f, text in prod.items()
            if f not in owner_files and pattern.search(text)
        ]
        if callers:
            continue
        # 同文件内的引用也算使用（B436）：`tools/surface.py` 里 `"fs.read": FsReadCall`
        # 这种注册表值、以及同文件的类型注解，以前全被排除（`f not in owner_files`），
        # 于是 89 个「死代码」里大半其实是**同文件在用**——按它删会删掉活代码。
        # 判定用「出现次数 > 1」：定义本身占一次，多于一次说明别处还在用。
        same_file_uses = [
            f for f, text in prod.items()
            if f in owner_files and len(pattern.findall(text)) > 1
        ]
        if same_file_uses:
            continue
        rows.append(
            {
                "symbol": name,
                "defined_in": owners,
                "test_refs": len(pattern.findall(test_blob)),
                "kind": "dead" if pattern.search(test_blob) else "unused",
            }
        )
    # `r["test_refs"]` 的值类型是 `object`（rows 是 `list[dict[str, object]]`）。
    # 显式转 int 之前先窄化，别让 `int(object)` 在运行时炸（B573）。
    def _sort_key(r: dict[str, Any]) -> tuple[bool, int, str]:
        refs = r["test_refs"]
        return (
            r["kind"] != "dead",
            -(refs if isinstance(refs, int) else 0),
            str(r["symbol"]),
        )

    return sorted(rows, key=_sort_key)


def _joined(value: object) -> str:
    """把 `rows[*]["defined_in"]` 折成逗号串。

    那一列的类型是 `object`（rows 是 `list[dict[str, object]]`），
    直接 `",".join(...)` 会被 mypy 拒，运行时也会在拿到非可迭代值时炸（B573）。
    """
    if isinstance(value, (list, tuple, set, frozenset)):
        return ",".join(str(v) for v in value)
    return str(value)


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
            print(f"  {r['symbol']:<34} tests={r['test_refs']:<3} {_joined(r['defined_in'])}")
        print()
    if unused:
        print("两侧都没出现（可能死代码，也可能是动态引用）：")
        for r in unused:
            print(f"  {r['symbol']:<34} {_joined(r['defined_in'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
