#!/usr/bin/env python3
"""一次性：把 B75 追加进 .veripen/batches.jsonl（只增不改）。"""
from __future__ import annotations

import json
from pathlib import Path

LEDGER = Path(__file__).resolve().parents[2] / ".veripen" / "batches.jsonl"

ROW = {
    "batch": "B75",
    "state": "done",
    "started": "2026-11-05",
    "ended": "2026-11-05",
    "agent": "little-code-sauce",
    "scope": "H01 能力矩阵回填：B35–B74 落地后首次全表核对，22 项逐条 grep 锚点",
    "files": [
        "plan-hos-ares/H01-能力矩阵与对标口径.md",
        "plan-hos-ares/MANIFEST.md",
        "PLANS.md",
        "scripts/plan/backfill_h01.py",
    ],
    "dod": "A(22 项每项有具名锚点) OK；B(状态与账本一致：18 ✅ / 3 ◐ / 1 ❌策略不做) OK；C(回填记录写明每项依据) OK；D(check-plan 锚点校验通过、structure OK) OK",
    "evidence": "python scripts/plan/backfill_h01.py；node plan-hos-ares/check-plan.cjs -> structure OK（能力矩阵 22 项）；node scripts/plan/check-plans.cjs -> governance OK",
    "open": "V4 召回 0.33 仍未排批次（H09 §4 开放问题 #3）",
    "notes": "核对方式是对 22 项逐条 grep 代码锚点、不采信账本自述；21/22 命中，G1 的 renderAudit 在前端 index.html 而非 console.py，已更正。锚点改用 file.py#符号 而非行号：22 个行号的维护成本高于它证明的事，代价是 check-plan 行号计数 27→21",
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
