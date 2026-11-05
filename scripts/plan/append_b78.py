#!/usr/bin/env python3
"""一次性：把 B78 追加进 .veripen/batches.jsonl（只增不改）。"""
from __future__ import annotations

import json
from pathlib import Path

LEDGER = Path(__file__).resolve().parents[2] / ".veripen" / "batches.jsonl"

ROW = {
    "batch": "B78",
    "state": "done",
    "started": "2026-11-05",
    "ended": "2026-11-05",
    "agent": "little-code-sauce",
    "scope": "录屏第五幕 chat：交互式 agent 进成片，指代与 diff 可见",
    "files": [
        "scripts/demo/record_terminal.py",
        "tests/unit/test_terminal_tour.py",
        "plan-hos-ares/H20-交互式Agent界面.md",
    ],
    "dod": "A(24.87s >= 22s) OK；B(五幕序 run/agent/platform/profile/chat) OK；C(画面含 客户档 / 档策略 / ⏺ 计划 三关键行) OK；D(两次运行序列与行数一致) OK；E(只连环回) OK",
    "evidence": "python scripts/demo/record_terminal.py -> 83 行 / 70 帧 / 24.87s；build_tour.py -> 成片 60.3s（expected 60.30s）；pytest tests/unit/test_terminal_tour.py -> 7 passed",
    "open": "",
    "notes": "chat 输出带 ANSI 颜色码，进 HTML 前必须剥（_ANSI 用 chr(27) 构造，源码里不留裸 ESC）；成片时长仍由 build_tour 现算，55.4→60.3 自动兜住",
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
