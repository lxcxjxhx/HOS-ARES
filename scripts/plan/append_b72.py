#!/usr/bin/env python3
"""一次性：把 B72 追加进 .veripen/batches.jsonl（只增不改）。"""
from __future__ import annotations

import json
from pathlib import Path

LEDGER = Path(__file__).resolve().parents[2] / ".veripen" / "batches.jsonl"

ROW = {
    "batch": "B72",
    "state": "done",
    "started": "2026-11-05",
    "ended": "2026-11-05",
    "agent": "little-code-sauce",
    "scope": "platform 接 profile：资产(符号→env) / oracle 白名单 / 审批策略 / engagement 身份从客户档来",
    "files": [
        "veripen_cli/cmd_platform.py",
        "tests/integration/test_platform_profile.py",
    ],
    "dod": "A(解析出资产与 oracle 白名单) OK；B(needs_approval 非空自动要求审批，不靠手工开关) OK；C(报告写 profile 名/customer/engagement/hash) OK；D(符号未注入退出 2 且列出需注入的变量名) OK；E(--profile 与 --targets 同时给退出 2) OK",
    "evidence": "pytest tests/integration/test_platform_profile.py -> 6 passed；实跑 profiles/demo-a 报告头：客户档 demo-a · DEMO-A-001 · ENG-2026-001 · 可用 oracle 9 类 / 需审批 3 类",
    "open": "",
    "notes": "白名单翻黑名单而不改 cmd_run 契约：exclude = ALL_ORACLES - profile.enabled；档决定要不要批、命令行决定谁签了字，两者分工不混",
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
