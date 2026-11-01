#!/usr/bin/env python3
"""ledger-check —— 批次账本的诚实性闸门。

两条规则，针对"标了 done 但实际不完整"这一类失真：

1. **done 的批次 open 里不许出现 `not implemented` / `不存在` 这类"缺件"描述**
   —— 缺件不是遗留问题，是没做完。
2. **`done(legacy-scope)` 必须带 scope_note** —— 说明它按哪一版口径完成，
   否则读者无法判断"done 到底覆盖了什么"。

用法：python scripts/lint/ledger_check.py [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / ".veripen" / "batches.jsonl"

#: 命中即"这是缺件，不是遗留"
MISSING_MARKERS = ("不存在", "not implemented", "未实现", "尚未实现", "缺表", "缺列")

#: 允许的状态值
STATES = {"todo", "doing", "done", "done(legacy-scope)", "blocked"}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    rows = [json.loads(line) for line in LEDGER.read_text(encoding="utf-8").splitlines() if line.strip()]
    problems: list[str] = []

    for row in rows:
        batch, state = row.get("batch", "?"), row.get("state", "")
        if state not in STATES:
            problems.append(f"{batch}: 未知状态 {state!r}")
        if state.startswith("done") and not row.get("dod"):
            problems.append(f"{batch}: done 但没有 DoD 证据")
        for item in row.get("open", []):
            if any(marker in item for marker in MISSING_MARKERS):
                problems.append(f"{batch}: open 里有缺件描述（应改状态或补实现）：{item[:60]}")
        if state == "done(legacy-scope)" and not row.get("scope_note"):
            problems.append(f"{batch}: done(legacy-scope) 缺 scope_note")

    if args.json:
        print(json.dumps({"total": len(rows), "problems": problems}, ensure_ascii=False, indent=2))
    else:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        for p in problems:
            print(f"LEDGER {p}")
        print(f"ledger-check: batches={len(rows)} problems={len(problems)}")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
