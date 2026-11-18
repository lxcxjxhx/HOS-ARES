#!/usr/bin/env python3
"""一次性：把 B82 追加进 .hos-ares/batches.jsonl（只增不改）。"""
from __future__ import annotations

import json
from pathlib import Path

LEDGER = Path(__file__).resolve().parents[2] / ".hos-ares" / "batches.jsonl"

ROW = {
    "batch": "B82",
    "state": "done",
    "started": "2026-11-05",
    "ended": "2026-11-05",
    "agent": "little-code-sauce",
    "scope": "统一 CLI 入口：hos_ares <子命令>（chat/run/platform/…共11个），argparse 零依赖，原样透传",
    "files": [
        "hos_ares_cli/__main__.py",
        "hos_ares_cli/__init__.py",
        "pyproject.toml",
        "tests/unit/test_cli_entry.py",
    ],
    "dod": "A(chat/run/platform/doctor 可调起且参数透传) OK；B(未知子命令 exit 2 并列出可用) OK；C(--help 列全 11 个子命令各有说明) OK；D(python -m hos_ares_cli.cmd_* 直调完全不变) OK；E(pyproject 声明 hos_ares 入口脚本) OK",
    "evidence": "pytest tests/unit/test_cli_entry.py -> 7 passed；python -m hos_ares_cli --help/--version/nosuch 手工验证；全量 unit 绿；noroute/nodata 0 命中",
    "open": "",
    "notes": "不引 click/typer：零依赖纪律（离线包要能装进内网）。透传规则是不解析不改写不补默认值——中间层一旦帮补参数，--help 与实际行为就分叉。顺手补 hos_ares_cli.__version__ 并让测试锁住它与 pyproject 一致",
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
