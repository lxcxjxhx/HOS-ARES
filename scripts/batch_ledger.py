"""批次账本（`21 §5`）：`.veripen/batches.jsonl` 是唯一进度事实源。

用法：
  python scripts/batch_ledger.py append --batch B1 --state done --dod "pytest tests/unit/test_contracts.py:exit=0"
  python scripts/batch_ledger.py status

规则（`21 §5`）：
  - 每批一行 JSON（追加，不覆盖历史）
  - 完成度 = done 批次数 / 17，且必须有 dod 证据
  - 没有证据的 done 不算
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / ".veripen" / "batches.jsonl"

#: 17 批的定义（`21 §2`），用于 status 的分母
BATCHES: dict[str, str] = {
    "B1": "骨架与契约",
    "B2": "调度",
    "B3": "guard",
    "B4": "证据",
    "B5": "TierA 前 4 类",
    "B6": "TierA 余 2 类",
    "B7": "TierB 5 类",
    "B8": "OpenAPI 生成器",
    "B9": "认证清单",
    "B10": "agent 循环",
    "B11": "报告三视图",
    "B12": "bench",
    "B13": "交付硬化",
    "B14": "TierC 3 类",
    "B15": "Web 控制台",
    "B16": "第二客户档",
    "B17": "交付物料",
}

WAVES: dict[str, list[str]] = {
    "W1 内核骨架": ["B1", "B2", "B3", "B4"],
    "W2 能力扩容": ["B5", "B6", "B7", "B8", "B9"],
    "W3 工程交付": ["B10", "B11", "B12", "B13"],
    "W4 平台化": ["B14", "B15", "B16", "B17"],
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=10, check=False
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def append(args: argparse.Namespace) -> int:
    if args.batch not in BATCHES:
        print(f"unknown batch {args.batch!r}; known: {sorted(BATCHES)}", file=sys.stderr)
        return 2

    dod = []
    for spec in args.dod or []:
        # 格式：`cmd:exit=N` 或 `cmd:exit=N:passed=X`
        parts = spec.rsplit(":", 2)
        cmd = parts[0]
        entry: dict[str, object] = {"cmd": cmd}
        for extra in parts[1:]:
            if "=" in extra:
                k, v = extra.split("=", 1)
                try:
                    entry[k] = int(v)
                except ValueError:
                    entry[k] = v
        dod.append(entry)

    if args.state == "done" and not dod:
        print("refusing to record state=done without dod evidence (21 §5 进度口径)", file=sys.stderr)
        return 1

    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "batch": args.batch,
        "name": BATCHES[args.batch],
        "state": args.state,
        "recorded": _now(),
        "commit": _git("rev-parse", "--short", "HEAD") or None,
        "files": args.files or [],
        "dod": dod,
        "evidence": args.evidence or [],
        "open": args.open or [],
        "notes": args.notes or "",
    }
    with LEDGER.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"recorded {args.batch} state={args.state} -> {LEDGER.relative_to(ROOT)}")
    return 0


def status(_: argparse.Namespace) -> int:
    latest: dict[str, dict] = {}
    if LEDGER.exists():
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            latest[rec["batch"]] = rec  # 后写的覆盖前面的

    done = 0
    print("HOS-ARES · 17 批进度（.veripen/batches.jsonl 是唯一事实源）\n")
    for wave, batches in WAVES.items():
        cells = []
        for b in batches:
            rec = latest.get(b)
            if rec and rec["state"] == "done" and rec.get("dod"):
                cells.append(f"▣{b}")
                done += 1
            elif rec and rec["state"] == "blocked":
                cells.append(f"✗{b}")
            elif rec and rec["state"] == "doing":
                cells.append(f"◐{b}")
            else:
                cells.append(f"▢{b}")
        print(f"  {wave:14s} {' '.join(cells)}")
    print(f"\n  完成度：{done}/17 批有 DoD 证据")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="批次账本")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("append", help="追加一条批次记录")
    a.add_argument("--batch", required=True)
    a.add_argument("--state", default="done", choices=["todo", "doing", "done", "blocked"])
    a.add_argument("--dod", action="append", help="cmd:exit=N[:passed=X]，可重复")
    a.add_argument("--files", action="append")
    a.add_argument("--evidence", action="append")
    a.add_argument("--open", action="append")
    a.add_argument("--notes", default="")
    a.set_defaults(func=append)

    s = sub.add_parser("status", help="显示进度看板")
    s.set_defaults(func=status)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
