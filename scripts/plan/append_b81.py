#!/usr/bin/env python3
"""一次性：把 B81 追加进 .veripen/batches.jsonl（只增不改）。"""
from __future__ import annotations

import json
from pathlib import Path

LEDGER = Path(__file__).resolve().parents[2] / ".veripen" / "batches.jsonl"

ROW = {
    "batch": "B81",
    "state": "done",
    "started": "2026-11-05",
    "ended": "2026-11-05",
    "agent": "little-code-sauce",
    "scope": "chat 接真模型路径：配 VERIPEN_MODEL_* 时先问模型，非法则降级 rules 并明说",
    "files": [
        "veripen_cli/cmd_chat.py",
        "tests/lab/model_server.py",
        "tests/integration/test_chat_model.py",
    ],
    "dod": "A(模型调用被采纳并执行) OK；B(未知工具/多调用/500 三种都降级并提示) OK；C(未配置仍 rules 且无降级提示) OK；D(横幅显示模型名) OK；E(本地假服务，只连环回) OK",
    "evidence": "pytest tests/integration/test_chat_model.py -> 7 passed；全量 unit 绿；noroute/nodata 0 命中",
    "open": "真模型的服从性未验（本机无模型后端，假服务只能验链路）",
    "notes": "修掉一处假接线：B77 只把模型名显示在横幅上、执行永远走 rules；另修一处真缺陷——最初用 to_tool_call 校验，但其白名单是 SCHEMAS∪surface_names（oracle 工具面），read 不在其中，合规调用被判非法全部降级；改为按 chat 自己的 CHAT_TOOLS 校验",
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
