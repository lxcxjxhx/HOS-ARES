#!/usr/bin/env python3
"""bindings-check —— 验收 24 §3：每个 op_id 都有绑定，每个 path_key 都可解析。

检查项：
 1. 结构合法（transport/method/path_key 齐备）—— 由 load_table 保证
 2. 覆盖完整：ALL_OPS 全部登记、无未知 op
 3. path_key 无重复（两个 op 共用一个键 = 迟早串线）
 4. 环境注入进度：报告仍未注入的键数；``--strict-env`` 时视为失败（部署/CI 用）
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.bindings import BindingsError, check_coverage, load_table, unresolved_keys  # noqa: E402
from api.ops import ALL_OPS  # noqa: E402

BINDINGS = ROOT / "config" / "bindings" / "service.yaml"


def main(argv: list[str]) -> int:
    strict = "--strict-env" in argv
    try:
        table = load_table(BINDINGS)
    except BindingsError as exc:
        print(f"bindings-check: FAIL: 绑定表不合法: {exc}")
        return 1

    problems = check_coverage(table, ALL_OPS)

    dupes = [k for k, n in Counter(b.path_key for b in table.bindings.values()).items() if n > 1]
    if dupes:
        problems.append("path_key 重复: " + ", ".join(sorted(dupes)))

    if problems:
        print(f"bindings-check: FAIL: {len(problems)} 项")
        for p in problems:
            print("  " + p)
        return 1

    pending = unresolved_keys(table)
    if strict and pending:
        print(f"bindings-check: FAIL: {len(pending)} 个 path_key 未注入环境")
        for k in pending:
            print("  " + k)
        return 1

    print(
        f"bindings-check: OK: {len(table.bindings)} op 绑定 / {len(ALL_OPS)} 期望；"
        f"环境待注入 {len(pending)} 项"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
