#!/usr/bin/env python3
"""一次性：把 B80 追加进 .veripen/batches.jsonl（只增不改）。"""
from __future__ import annotations

import json
from pathlib import Path

LEDGER = Path(__file__).resolve().parents[2] / ".veripen" / "batches.jsonl"

ROW = {
    "batch": "B80",
    "state": "done",
    "started": "2026-11-05",
    "ended": "2026-11-05",
    "agent": "little-code-sauce",
    "scope": "多步计划与会话延续：一句话两三步（然后/接着）、失败即停、/history",
    "files": [
        "veripen_core/agent/intent.py",
        "veripen_cli/cmd_chat.py",
        "tests/unit/test_chat_multistep.py",
    ],
    "dod": "A(含然后/接着产多步计划) OK；B(任一步失败立即停止并说明停在第几步/剩几步) OK；C(--resume 启动显示历史) OK；D(/history 列出步骤) OK；E(单句单步与 B77 一致) OK",
    "evidence": "pytest tests/unit/test_chat_multistep.py -> 7 passed；全量 unit 绿；noroute/nodata 0 命中",
    "open": "",
    "notes": "多步不进 Loop：Loop 的 tool_calls_per_step=1 是判定骨架纪律（允许多调用=允许一步内越权串动作）；多步由 Plan.steps 承载，每步仍是一次单一调用。三处要做对：第二段沿用第一段路径、最后一步失败也要明说、任一段解析不出就退回单步不半执行",
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
