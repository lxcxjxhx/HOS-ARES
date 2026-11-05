#!/usr/bin/env python3
"""一次性：把 B69 追加进 .veripen/batches.jsonl（只增不改）。"""
from __future__ import annotations

import json
from pathlib import Path

LEDGER = Path(__file__).resolve().parents[2] / ".veripen" / "batches.jsonl"

ROW = {
    "batch": "B69",
    "state": "done",
    "started": "2026-11-05",
    "ended": "2026-11-05",
    "agent": "little-code-sauce",
    "scope": "终端体验录屏：run/agent/platform 三幕真跑过程 → 15s 视频 + 42 帧 + 可复现 manifest",
    "files": [
        "scripts/demo/record_terminal.py",
        "tests/unit/test_terminal_tour.py",
        "docs/delivery/terminal-tour.md",
    ],
    "dod": "A(15.07s/1280x720/H.264) OK；B(三条命令串在画面) OK；C(VERIFIED 2 / ok  write / 汇总：2 资产 三关键行可见) OK；D(两次运行场景序列与行数一致) OK；E(靶场 Loopback，只连 127.0.0.1) OK",
    "evidence": "python scripts/demo/record_terminal.py；pytest tests/unit/test_terminal_tour.py -> 7 passed；ffprobe 15.07s",
    "open": "无打字动画与光标（有意取舍：内容是证据，动画不是）",
    "notes": "三个坑都进了注释：①精简 env 让 Windows socket 初始化失败(WinError 10106)，必须基于 os.environ 复制；②管道默认 locale 编码，需 PYTHONUTF8=1；③命令串在 scene.prompt 而非 stdout，判据 B 要一起读",
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
