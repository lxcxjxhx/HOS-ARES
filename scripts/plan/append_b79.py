#!/usr/bin/env python3
"""一次性：把 B79 追加进 .veripen/batches.jsonl（只增不改）。"""
from __future__ import annotations

import json
from pathlib import Path

LEDGER = Path(__file__).resolve().parents[2] / ".veripen" / "batches.jsonl"

ROW = {
    "batch": "B79",
    "state": "done",
    "started": "2026-11-05",
    "ended": "2026-11-05",
    "agent": "little-code-sauce",
    "scope": "对话面板接三支柱：chat 里说「跑一遍」/「按档跑」→ cmd_run / cmd_platform",
    "files": [
        "veripen_core/agent/intent.py",
        "veripen_cli/cmd_chat.py",
        "tests/integration/test_chat_hunt.py",
    ],
    "dod": "A(跑一下 target openapi=path 调起 run 并产出报告) OK；B(按档跑调起 platform) OK；C(缺 openapi/缺档明确追问不猜) OK；D(输出折叠≤12行+完整日志路径) OK；E(摘要进 task_events) OK",
    "evidence": "pytest tests/integration/test_chat_hunt.py -> 6 passed；全量 unit 绿；noroute/nodata 0 命中；doctor fail=0",
    "open": "chat 里的 platform 仍需 VP_ASSET_* 注入，未在对话里引导设置",
    "notes": "执行器直接调既有命令不重写链路（同一份逻辑两个入口）；期间修两处：注释里出现客户档名撞 test_no_profile_specific_code_paths（注释也不行，示例用 <档名>）、测试拼裸 host 撞 noroute",
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
