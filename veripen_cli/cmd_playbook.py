"""`veripen-cli playbook`（B50）：校验打法资产，不执行。

执行在 `scripts/b50/playbook_run.py`（演练夹具），这里只做**门**：
schema、oracle 存在性、impact 一致性、成功率与实测一致。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from veripen_core.playbooks import PlaybookError, load_all
from veripen_oracles import ALL_ORACLES


def oracle_table() -> dict[str, str]:
    """oracle id → impact。放在 CLI：`veripen_core` 不许 import `veripen_oracles`。"""
    return {oracle.meta.id: oracle.meta.impact.value for oracle in ALL_ORACLES}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="playbook")
    sub = ap.add_subparsers(dest="cmd", required=True)
    check = sub.add_parser("check", help="校验 playbooks/")
    check.add_argument("--dir", default=None)
    args = ap.parse_args(argv)

    if args.cmd != "check":
        print(f"未知子命令 {args.cmd}", file=sys.stderr)
        return 2

    try:
        books = load_all(Path(args.dir) if args.dir else None, oracle_table=oracle_table())
    except PlaybookError as exc:
        print(f"playbook-check: FAIL: {exc.code}: {exc.detail}")
        return 1

    print(f"playbook-check: OK: {len(books)} 个资产")
    for book in books:
        rate = "未测" if book.success_rate is None else f"{book.success_rate:.4f}"
        print(f"  {book.id}  oracle={book.oracle}  success_rate={rate}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
