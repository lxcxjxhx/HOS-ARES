#!/usr/bin/env python3
"""一次性：把 B76 追加进 .veripen/batches.jsonl（只增不改）。"""
from __future__ import annotations

import json
from pathlib import Path

LEDGER = Path(__file__).resolve().parents[2] / ".veripen" / "batches.jsonl"

ROW = {
    "batch": "B76",
    "state": "done",
    "started": "2026-11-05",
    "ended": "2026-11-05",
    "agent": "little-code-sauce",
    "scope": "结构标签接线：plan(accept_structural) 采纳 parser 的 proposed，无显式标签的契约也能进候选",
    "files": [
        "veripen_contract/planner.py",
        "veripen_cli/cmd_run.py",
        "veripen_cli/cmd_platform.py",
        "tests/unit/test_structural_confirm.py",
    ],
    "dod": "A(带开关候选数严格增加) OK；B(采纳标签 source=structural / conf 0.80 < 契约 0.95) OK；C(response_shape.tag_sources 逐条可辨) OK；D(不带开关行为逐字不变) OK；E(靶场实测可跑 oracle 1->3，VERIFIED 2) OK",
    "evidence": "pytest tests/unit/test_structural_confirm.py -> 6 passed；out/demo_recall 实跑 BEFORE 1 绑定 1 oracle rc=1 / AFTER 2 绑定 3 oracle rc=0",
    "open": "B30 真实目标的 0.33 需复测才能更新外部指标",
    "notes": "推断器本来就存在（parser._structural_tags 10 类规则），缺的是 plan() 的 confirm 从来没有调用方——与 B62 injectable()、B63 with_knowledge() 同一类接线欠账；采纳后 source 保持 structural 不抹成 contract，猜错时要能一眼看出",
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
