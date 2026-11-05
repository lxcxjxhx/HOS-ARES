#!/usr/bin/env python3
"""一次性：把 B74 追加进 .veripen/batches.jsonl（只增不改）。"""
from __future__ import annotations

import json
from pathlib import Path

LEDGER = Path(__file__).resolve().parents[2] / ".veripen" / "batches.jsonl"

ROW = {
    "batch": "B74",
    "state": "done",
    "started": "2026-11-05",
    "ended": "2026-11-05",
    "agent": "little-code-sauce",
    "scope": "报告面的显示：route_short 保留 scheme+来源段+id前8位，不再只有 route://",
    "files": [
        "veripen/report/render.py",
        "tests/unit/test_report_route_short.py",
    ],
    "dod": "A(route://contract/<hex> -> route://contract/<前8位>) OK；B(无 scheme 的 ref 仍前 8 位) OK；C(空 ref 返回空串) OK；D(完整 id 不透出) OK",
    "evidence": "pytest tests/unit/test_report_route_short.py -> 5 passed；实跑 out/demo-run/report.md 显示 `route://contract/5ab365e3`；全量 unit 绿",
    "open": "",
    "notes": "根因是 ROUTE_PREFIX_LEN=8 恰好等于 route:// 的长度，截断落在分隔符后，导致所有面显示相同；改按 scheme+source+id[:8] 拼而不整体截断",
}


def main() -> int:
    existing = {
        json.loads(line)["batch"]
        for line in LEDGER.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    if ROW["batch"] in existing:
        print(f"skip {ROW['batch']}（已存在）")
        return 0
    with LEDGER.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(ROW, ensure_ascii=False) + "\n")
    print("appended 1 row")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
