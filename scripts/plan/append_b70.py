#!/usr/bin/env python3
"""一次性：把 B70 追加进 .veripen/batches.jsonl（只增不改）。"""
from __future__ import annotations

import json
from pathlib import Path

LEDGER = Path(__file__).resolve().parents[2] / ".veripen" / "batches.jsonl"

ROW = {
    "batch": "B70",
    "state": "done",
    "started": "2026-11-05",
    "ended": "2026-11-05",
    "agent": "little-code-sauce",
    "scope": "成片与一键演示：控制台 10 页 + 三幕终端 + 片头章节卡 → 50.5s 成片；make demo 一键",
    "files": [
        "scripts/demo/build_tour.py",
        "tests/unit/test_tour_build.py",
        "docs/delivery/tour.md",
        "Makefile",
    ],
    "dod": "A(50.5s/1280x720/H.264) OK；B(片头+2章节卡+片尾共6段齐) OK；C(成片50.50s = 各段之和50.50s，误差0) OK；D(段落序列固定，make demo 可重跑) OK；E(素材全本地生成，无下载) OK",
    "evidence": "python scripts/demo/build_tour.py；pytest tests/unit/test_tour_build.py -> 7 passed；ffprobe 50.5s",
    "open": "",
    "notes": "卡片 PNG 与 mp4 混进同一 concat 会花屏：-loop 1 必须配 -framerate，且 -t 要放在 -i 之后（输出时长），放输入前会被解释成'读输入多久'，单图输入直接报错退出。流程改为卡片先各自转同参数 mp4 再 concat",
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
