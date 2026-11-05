#!/usr/bin/env python3
"""一次性：把 B73 追加进 .veripen/batches.jsonl（只增不改）。"""
from __future__ import annotations

import json
from pathlib import Path

LEDGER = Path(__file__).resolve().parents[2] / ".veripen" / "batches.jsonl"

ROW = {
    "batch": "B73",
    "state": "done",
    "started": "2026-11-05",
    "ended": "2026-11-05",
    "agent": "little-code-sauce",
    "scope": "录屏第四幕：platform --profile（按客户档跑），档身份与档策略进画面",
    "files": [
        "scripts/demo/record_terminal.py",
        "veripen_cli/cmd_platform.py",
        "tests/unit/test_terminal_tour.py",
    ],
    "dod": "A(19.93s >= 18s) OK；B(四幕序 run/agent/platform/profile) OK；C(画面含 客户档： 与 档策略：可用 oracle) OK；D(两次运行场景序列与行数一致) OK；E(VP_ASSET_WEB_01 注入 lab 地址，只连环回) OK",
    "evidence": "python scripts/demo/record_terminal.py -> 70 行 / 56 帧 / 19.93s；build_tour.py -> 成片 55.37s（expected 55.37s）；pytest tests/unit/test_terminal_tour.py -> 7 passed",
    "open": "",
    "notes": "期间真改进：cmd_platform 原先只把档身份写进报告文件、终端不打印，操作员看不到按哪个档在跑；本批加启动打印，判据 C 才有可看的行。成片时长由 build_tour 现算，B70 判据 C 自动兜住 50.5→55.37 的变化",
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
