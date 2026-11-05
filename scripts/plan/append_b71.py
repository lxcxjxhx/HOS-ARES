#!/usr/bin/env python3
"""一次性：把 B71 追加进 .veripen/batches.jsonl（只增不改）。"""
from __future__ import annotations

import json
from pathlib import Path

LEDGER = Path(__file__).resolve().parents[2] / ".veripen" / "batches.jsonl"

ROW = {
    "batch": "B71",
    "state": "done",
    "started": "2026-11-05",
    "ended": "2026-11-05",
    "agent": "little-code-sauce",
    "scope": "MANIFEST 行尾根治：生成与校验两端统一按 LF 计算，CRLF 工作区不再假红",
    "files": [
        "plan-hos-ares/check-plan.cjs",
        "plan-veripen/_gen_manifest.cjs",
        "plan-veripen/_verify-manifest.cjs",
        "plan-hos-ares/MANIFEST.md",
        "plan-veripen/MANIFEST.md",
        "PLANS.md",
    ],
    "dod": "A(干净 clone 的 check-plan 不再报过期) OK；B(把某文件转 CRLF 后校验仍 OK) OK；C(两个包的注册表 sha16 已同步) OK",
    "evidence": "node plan-hos-ares/check-plan.cjs（CRLF 态 structure OK）；cd plan-veripen && node _verify-manifest.cjs -> OK 54 files；干净 clone 复验",
    "open": "",
    "notes": "同一债复发第三次（B60 修过工作区、B65 修过一次重生成），这次修在脚本层：generate 与 verify 都先 normalize(CRLF->LF) 再算 sha，行尾从此不影响内容寻址",
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
