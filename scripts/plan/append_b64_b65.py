#!/usr/bin/env python3
"""一次性：把 B64 / B65 追加进 .veripen/batches.jsonl（只增不改）。"""
from __future__ import annotations

import json
from pathlib import Path

LEDGER = Path(__file__).resolve().parents[2] / ".veripen" / "batches.jsonl"

ROWS = [
    {
        "batch": "B64",
        "state": "done",
        "started": "2026-11-04",
        "ended": "2026-11-05",
        "agent": "little-code-sauce",
        "scope": "ui.audit 接 task_events：只读页从恒空变有源，六键脱敏，空库显式 unavailable",
        "files": [
            "veripen_core/audit_source.py",
            "veripen_cli/cmd_console.py",
            "tests/unit/test_audit_source.py",
            "scripts/lint/dead_exports.py",
        ],
        "dod": "A(有事件→source=state且count一致) OK；B(空库→unavailable且count=0) OK；C(每行仅六键，detail不透传) OK；D(审批四态可读who/what/asset) OK；E(写方法仍405) OK",
        "evidence": "pytest tests/unit/test_audit_source.py -> 8 passed；noroute/nodata 0 命中；tests/unit 全绿",
        "open": "",
        "notes": "H09 §4 开放问题 #2 收口；附 dead_exports.py：找\"只在 tests/ 里被调用\"的公开符号——必须含类方法，只扫顶层会把 injectable/with_knowledge 那三类欠账全部漏掉",
    },
    {
        "batch": "B65",
        "state": "done",
        "started": "2026-11-05",
        "ended": "2026-11-05",
        "agent": "little-code-sauce",
        "scope": "控制台巡检录屏：10 页真数据真渲染，产出 mp4 + PNG + 可复现 manifest",
        "files": [
            "scripts/demo/record_console.py",
            "tests/unit/test_demo_manifest.py",
            "tests/unit/test_ui_dom.py",
            "web/static/index.html",
            "web/static/__baselines__/renderers.txt",
            "docs/delivery/console-tour.md",
        ],
        "dod": "A(27.43s/1280x720/H.264) OK；B(10 页按 OPS 顺序) OK；C(findings=3/events=11/approvals=4 全非空) OK；D(两次运行 page_order 与 counts 逐字相同) OK；E(端口 18080 起顺序取，只连 loopback) OK",
        "evidence": "python scripts/demo/record_console.py；ffprobe out/demo/console-tour.mp4；两次跑 manifest 比对 page_order/counts 一致；pytest tests/unit/test_demo_manifest.py -> 7 passed",
        "open": "动画与过渡未录（页面本无过渡，取舍为零）",
        "notes": "录屏暴露三处真缺陷并修掉：audit 页从未有 renderAudit（落 renderGeneric 裸 JSON）、bind(0) 分到 6667 被 Chromium 判 ERR_UNSAFE_PORT、演示数据用 1970 秒让 pending 当场显示成 expired；renderers 基线同步 +5 行并锁进 test_ui_dom",
    },
]


def main() -> int:
    existing = {
        json.loads(line)["batch"]
        for line in LEDGER.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    added = 0
    with LEDGER.open("a", encoding="utf-8") as fh:
        for row in ROWS:
            if row["batch"] in existing:
                print(f"skip {row['batch']}（已存在）")
                continue
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            added += 1
    print(f"appended {added} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
