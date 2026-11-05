#!/usr/bin/env python3
"""一次性：把 B83 追加进 .veripen/batches.jsonl（只增不改）。"""
from __future__ import annotations

import json
from pathlib import Path

LEDGER = Path(__file__).resolve().parents[2] / ".veripen" / "batches.jsonl"

ROW = {
    "batch": "B83",
    "state": "done",
    "started": "2026-11-05",
    "ended": "2026-11-05",
    "agent": "little-code-sauce",
    "scope": "项目入口文档：顶层 README（3 分钟上手 / 三支柱 / 诚实边界 / 文档地图）+ 命令一致性机器校验",
    "files": [
        "README.md",
        "tests/unit/test_readme.py",
    ],
    "dod": "A(首屏三步：pip install / veripen doctor / veripen chat) OK；B(三支柱各一条命令+产物) OK；C(README 命令与 COMMANDS 表机器校验一致) OK；D(不能做什么一节列卡点，不用含糊话) OK；E(每条 veripen 子命令都在 COMMANDS 里) OK",
    "evidence": "pytest tests/unit/test_readme.py -> 6 passed；全量 unit 绿；noroute/nodata 0 命中；doctor fail=0",
    "open": "",
    "notes": "93 个 PR 后仓库没有 README，clone 下来不知道从哪开始。文档要机器校验：从 README 抽 veripen <名字> 比 COMMANDS 表，对不上就红——文档最常见腐烂方式是写得当时对、之后没人改。踩到一个自指陷阱：README 写'不写敬请期待'，而测试断言该词不在文中",
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
