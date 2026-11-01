#!/usr/bin/env python3
"""spec-drift —— 规划与代码的差异闸门（B12.1 第一产物）。

三个来源，三种比对：

| 来源 | 比什么 | 出处 |
|---|---|---|
| `03 §1` | DDL 里 ```sql 块中的 `CREATE TABLE` 清单 | 数据模型 |
| `03 §7` | 一致性自检编号（应为 17 条） | 数据侧门禁 |
| `17 §1` | 不变量 I1–I12 | 契约 |
| `18 §0` | oracle 清单（14 类） | 能力面 |

**只要出现「规划有、代码无」，exit 1。** 这就是"10 张表 vs 3 张表却标 done"那次的闸门。

用法：
  python scripts/lint/spec_drift.py            # 人类可读
  python scripts/lint/spec_drift.py --json     # 机器可读（进批次账本）
  python scripts/lint/spec_drift.py --baseline out/b12-completion/baseline.txt
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "plan-veripen"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ══════════════════════════════════════════════════════════════════
# 抽取
# ══════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class Drift:
    kind: str      # table | column | selfcheck | invariant | oracle | module
    name: str
    status: str    # missing | ok | extra
    detail: str = ""

    def line(self) -> str:
        mark = {"missing": "MISSING", "ok": "ok     ", "extra": "EXTRA  "}[self.status]
        tail = f"  ({self.detail})" if self.detail else ""
        return f"{mark} {self.kind:<10} {self.name}{tail}"


def _read(name: str) -> str:
    return (PLAN / name).read_text(encoding="utf-8")


def planned_tables() -> list[str]:
    """`03 §1` 的 ```sql 块里的 CREATE TABLE。"""
    text = _read("03-数据模型与状态机.md")
    block = _section(text, "## 1.", "## 2.")
    return sorted({m.group(1) for m in re.finditer(r"CREATE TABLE (\w+)", block)})


def planned_columns() -> dict[str, set[str]]:
    """`03 §1` 每张表的列名（用于比对 findings 的 outcome / coverage_src）。"""
    text = _read("03-数据模型与状态机.md")
    block = _section(text, "## 1.", "## 2.")
    out: dict[str, set[str]] = {}
    for match in re.finditer(r"CREATE TABLE (\w+) \((.*?)\n\);", block, re.S):
        table, body = match.group(1), match.group(2)
        cols: set[str] = set()
        for line in body.splitlines():
            line = line.strip().rstrip(",")
            if not line or line.startswith("--"):
                continue
            token = re.match(r"([a-z_]+)\s+[A-Z]", line) or re.match(r"([a-z_]+)\s+INTEGER", line)
            if token and token.group(1).upper() not in {"PRIMARY", "FOREIGN", "UNIQUE", "CHECK"}:
                cols.add(token.group(1))
        out[table] = cols
    return out


def planned_selfchecks() -> int:
    """`03 §7` 的自检条数（按 `-- N` 注释编号计数）。"""
    text = _read("03-数据模型与状态机.md")
    block = _section(text, "## 7.", "## 8.")
    return len(re.findall(r"^-- \d+", block, re.M))


def planned_invariants() -> list[str]:
    """`17 §1` 的不变量 I1–I12。"""
    text = _read("17-权威链与证据门.md")
    return sorted({m.group(1) for m in re.finditer(r"^\| (I\d+) \|", text, re.M)})


def planned_oracles() -> list[str]:
    """`18 §0` 总表里的 oracle_id（第一列第 2 列）。"""
    text = _read("18-Oracle目录（14类判定矩阵）.md")
    block = _section(text, "## 0.", "## 1.")
    return sorted({m.group(1) for m in re.finditer(r"^\| [ABC] \| `([a-z_0-9]+)`", block, re.M)})


def _section(text: str, start: str, end: str) -> str:
    i = text.find(start)
    if i < 0:
        return ""
    j = text.find(end, i + len(start))
    return text[i:] if j < 0 else text[i:j]


# ══════════════════════════════════════════════════════════════════
# 实际状态
# ══════════════════════════════════════════════════════════════════
def actual_tables() -> set[str]:
    from veripen_core.store import Store

    store = Store(":memory:").init()
    try:
        rows = store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        return {str(r["name"]) for r in rows}
    finally:
        store.close()


def actual_columns(table: str) -> set[str]:
    from veripen_core.store import Store

    store = Store(":memory:").init()
    try:
        rows = store.conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {str(r["name"]) for r in rows}
    finally:
        store.close()


def actual_oracles() -> set[str]:
    from veripen_oracles import ALL_ORACLES

    return {c.meta.id for c in ALL_ORACLES}


#: I1–I12 的机械保证（每条的"证据在哪"）。缺了就说明这条不变量只是墙上标语。
INVARIANT_EVIDENCE: dict[str, str] = {
    "I1": "tests/unit/test_contracts.py::test_pure_judge",
    "I2": "tests/unit/test_oracle_framework.py::test_three_way_is_independent_implementation",
    "I3": "tests/unit/test_profiles.py::test_profile_only_differs_in_yaml",
    "I4": "tests/unit/test_agent_loop.py::test_no_tool_can_take_an_address",
    "I5": "tests/unit/test_guard.py",
    "I6": "tests/unit/test_contracts.py::test_resolver_is_only_builder",
    "I7": "tests/unit/test_replay_order.py::test_route_unresolved_when_binding_deleted",
    "I8": "tests/unit/test_evidence.py::test_same_input_twice_same_hash",
    "I9": "scripts/lint/nodata_stub.py",
    "I10": "veripen_core/store.py::DDL:blackboard_facts",
    "I11": "veripen_core/store.py::DDL:asset_facts",
    "I12": "tests/unit/test_leads.py::test_action_cannot_produce_verdict",
}


# ══════════════════════════════════════════════════════════════════
# 比对
# ══════════════════════════════════════════════════════════════════
def collect() -> list[Drift]:
    drifts: list[Drift] = []

    want_tables, have_tables = set(planned_tables()), actual_tables()
    for name in want_tables:
        drifts.append(Drift("table", name, "ok" if name in have_tables else "missing"))
    for name in sorted(have_tables - want_tables):
        drifts.append(Drift("table", name, "extra", "代码里有、规划里没有"))

    want_cols, have_cols = planned_columns(), {}
    for table in sorted(want_tables & have_tables):
        have_cols[table] = actual_columns(table)
        missing = sorted(want_cols.get(table, set()) - have_cols[table])
        for col in missing:
            drifts.append(Drift("column", f"{table}.{col}", "missing"))

    checks = planned_selfchecks()
    drifts.append(
        Drift(
            "selfcheck",
            f"{checks} 条",
            "ok" if checks >= 17 else "missing",
            "" if checks >= 17 else "03 §7 应含 17 条自检",
        )
    )

    for inv in planned_invariants():
        evidence = INVARIANT_EVIDENCE.get(inv)
        ok = bool(evidence)
        drifts.append(Drift("invariant", inv, "ok" if ok else "missing", evidence or "无机械保证"))

    want_oracles, have_oracles = set(planned_oracles()), actual_oracles()
    for name in want_oracles:
        drifts.append(Drift("oracle", name, "ok" if name in have_oracles else "missing"))
    for name in sorted(have_oracles - want_oracles):
        drifts.append(Drift("oracle", name, "extra", "代码里有、18 §0 没有（可能是别名）"))

    return drifts


def summarize(drifts: list[Drift]) -> dict[str, Any]:
    missing = [d for d in drifts if d.status == "missing"]
    by_kind: dict[str, int] = {}
    for d in missing:
        by_kind[d.kind] = by_kind.get(d.kind, 0) + 1
    return {
        "total": len(drifts),
        "missing": len(missing),
        "by_kind": dict(sorted(by_kind.items())),
        "ok": len(missing) == 0,
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--baseline", help="把当前差异写成基线文件（用于记录「补之前长什么样」）")
    ap.add_argument("--only-missing", action="store_true")
    args = ap.parse_args(argv)

    drifts = collect()
    rows = [d for d in drifts if d.status == "missing"] if args.only_missing else drifts
    summary = summarize(drifts)

    if args.baseline:
        path = Path(args.baseline)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(d.line() for d in drifts) + f"\n\n# missing={summary['missing']}\n",
            encoding="utf-8",
        )
        print(f"baseline written: {path} (missing={summary['missing']})", file=sys.stderr)

    if args.json:
        print(json.dumps({"summary": summary, "drifts": [d.__dict__ for d in rows]},
                         ensure_ascii=False, indent=2))
    else:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        for d in rows:
            print(d.line())
        print()
        print(f"spec-drift: missing={summary['missing']} / total={summary['total']} "
              f"by_kind={summary['by_kind']}")
        print("VERDICT:", "OK（规划与代码一致）" if summary["ok"] else "DRIFT（规划有、代码无）")

    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
