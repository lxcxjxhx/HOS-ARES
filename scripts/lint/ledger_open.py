#!/usr/bin/env python3
"""列出账本里的未结项（`open` 非空）。

账本是 append-only 的，所以"这件事后来解决了"不能回改历史 —— 只能另立归口
（`plan-hos-ares/H31-未结项归口.md`）。这个脚本负责把待审清单拉出来。

用法：
    python scripts/lint/ledger_open.py              # 全部
    python scripts/lint/ledger_open.py --since B98  # 只看某一批之后
    python scripts/lint/ledger_open.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / ".veripen" / "batches.jsonl"


def _rows() -> list[dict[str, object]]:
    if not LEDGER.is_file():
        return []
    out: list[dict[str, object]] = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _order(batch: str) -> float:
    """B98 / B12.16 / H29 之类混排时给个可比顺序：主号优先，副号次之。"""
    match = re.match(r"^[A-Z]*(\d+)(?:\.(\d+))?", batch)
    if not match:
        return -1.0
    major = int(match.group(1))
    minor = int(match.group(2) or 0)
    return major + minor / 100.0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="ledger_open")
    ap.add_argument("--since", default="", help="只看这个批次之后的（含）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    floor = _order(args.since) if args.since else -1.0
    items = []
    for row in _rows():
        raw = row.get("open")
        if not raw:
            continue
        batch = str(row.get("batch") or "?")
        if _order(batch) < floor:
            continue
        text = raw if isinstance(raw, str) else " | ".join(str(x) for x in raw)
        items.append({"batch": batch, "open": text.strip()})

    if args.json:
        print(json.dumps(items, ensure_ascii=False, indent=2))
        return 0

    print(f"未结项 {len(items)} 条" + (f"（自 {args.since} 起）" if args.since else ""))
    for item in items:
        print(f"  [{item['batch']}] {item['open'][:110]}")
    print("→ 归口写在 plan-hos-ares/H31-未结项归口.md：已解决 / 有意取舍 / 真欠账")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
