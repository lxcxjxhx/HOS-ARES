#!/usr/bin/env python3
"""未结项分诊助手：把账本里的 `open` 和当前代码对上号。

它不是判定器，只做一件事：**从 open 文本里抽出可识别的符号名，看它们现在还在不在**。

- 符号出现在代码里 → 可能那件事已经做了（**候选：已解决**，需人核）
- 符号一个都找不到 → 可能仍然欠着（**候选：仍欠**）
- 抽不出符号 → 无从判断（多半是叙述性描述）

`plan-hos-ares/H31-未结项归口.md` 的判定靠人；这个脚本只是把"要人核什么"筛出来。

用法：
    python scripts/lint/ledger_triage.py --since B1 --until B84
    python scripts/lint/ledger_triage.py --batch B12.11
    python scripts/lint/ledger_triage.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / ".veripen" / "batches.jsonl"
SKIP_DIRS = {"out", ".venv", "vendor", ".git", "node_modules", "__pycache__"}

#: 反引号里的名字最可靠；再补两类常见标识符（CamelCase / 带下划线的函数名）。
_BACKTICK = re.compile(r"`([A-Za-z_][A-Za-z0-9_.]{2,})`")
_IDENT = re.compile(r"\b([A-Z][A-Za-z0-9]{3,}|[a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b")
#: 太常见、没有区分度的词，出现也不能说明问题解决了。
_STOP = {
    "true", "false", "none", "null", "str", "int", "dict", "list", "tuple",
    "todo", "plan", "route", "routeset", "oracle", "bench", "capsule",
    "docker", "wsl", "http", "https", "json", "yaml", "sqlite", "redis",
}


def _sources() -> list[Path]:
    out = []
    for path in ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        out.append(path)
    return out


def _symbols(text: str) -> list[str]:
    found = {m.group(1) for m in _BACKTICK.finditer(text)}
    found |= {m.group(1) for m in _IDENT.finditer(text)}
    return sorted({s for s in found if s.lower() not in _STOP and len(s) >= 4})


def _rows() -> list[dict[str, object]]:
    if not LEDGER.is_file():
        return []
    rows = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _order(batch: str) -> float:
    match = re.match(r"^[A-Z]*(\d+)(?:\.(\d+))?", batch)
    if not match:
        return -1.0
    return int(match.group(1)) + int(match.group(2) or 0) / 100.0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="ledger_triage")
    ap.add_argument("--since", default="")
    ap.add_argument("--until", default="")
    ap.add_argument("--batch", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    lo = _order(args.since) if args.since else -1.0
    hi = _order(args.until) if args.until else 1e9
    corpus = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in _sources())

    results = []
    for row in _rows():
        batch = str(row.get("batch") or "?")
        raw = row.get("open")
        if not raw:
            continue
        if args.batch and batch != args.batch:
            continue
        if not (_order(batch) >= lo and _order(batch) <= hi):
            continue
        text = raw if isinstance(raw, str) else " | ".join(str(x) for x in raw)
        symbols = _symbols(text)
        present = [s for s in symbols if s in corpus]
        if not symbols:
            verdict = "unjudgeable"
        elif present:
            verdict = "maybe_resolved"
        else:
            verdict = "maybe_open"
        results.append({
            "batch": batch,
            "verdict": verdict,
            "symbols": symbols,
            "present": present,
            "open": text.strip()[:400],
        })

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0

    counts: dict[str, int] = {}
    for item in results:
        counts[item["verdict"]] = counts.get(item["verdict"], 0) + 1
    print(f"分诊 {len(results)} 条：" + " / ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print()
    for item in results:
        mark = {"maybe_resolved": "可能已解决", "maybe_open": "可能仍欠",
                "unjudgeable": "无法判断"}[item["verdict"]]
        print(f"[{item['batch']}] {mark}")
        if item["present"]:
            print(f"    代码里找到：{', '.join(item['present'][:6])}")
        elif item["symbols"]:
            print(f"    找不到：{', '.join(item['symbols'][:6])}")
        print(f"    {item['open'][:120]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
