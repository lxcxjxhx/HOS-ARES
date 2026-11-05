#!/usr/bin/env python3
"""一次性：把 B77 追加进 .veripen/batches.jsonl（只增不改）。"""
from __future__ import annotations

import json
from pathlib import Path

LEDGER = Path(__file__).resolve().parents[2] / ".veripen" / "batches.jsonl"

ROW = {
    "batch": "B77",
    "state": "done",
    "started": "2026-11-05",
    "ended": "2026-11-05",
    "agent": "little-code-sauce",
    "scope": "交互式 agent 界面：说人话 → 计划 → 工具流 → diff；可中断、上下文累计、决策层如实渲染",
    "files": [
        "veripen_cli/cmd_chat.py",
        "veripen_core/agent/intent.py",
        "veripen_core/agent/session.py",
        "veripen_cli/_io.py",
        "veripen_core/workbench/executor.py",
        "tests/unit/test_chat_intent.py",
        "tests/integration/test_chat_session.py",
    ],
    "dod": "A(自然语言→工具，不要求知道工具名) OK；B(计划先于执行且可否决) OK；C(写/改后显示 +N -M 与 diff 正文) OK；D(Ctrl+C 只中断当前轮) OK；E(决策层如实渲染 model: rules) OK；F(第二轮能引用第一轮结果) OK",
    "evidence": "pytest tests/unit/test_chat_intent.py -> 10 passed；pytest tests/integration/test_chat_session.py -> 6 passed；手工冒烟 write/edit/read/sh/status 全通",
    "open": "本机无模型后端（~/.litellm 不存在、4000 不通），当前跑 rules 层；配 VERIPEN_MODEL_* 后同界面上模型",
    "notes": "四个真 bug：①setup_stdout 只切 stdout 没切 stdin，中文动词按 GBK 解码成乱码导致每句都被当成读；②record 遇孤立代理项抛 surrogates not allowed 打崩会话；③写意图只按含'写'字判断，'看下 x.txt 里写了什么'被判成 write；④Windows 下 cmd -lc 不存在，执行 python -V 永远 exit=1",
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
