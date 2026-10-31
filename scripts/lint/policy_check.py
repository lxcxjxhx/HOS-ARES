#!/usr/bin/env python3
"""lint-policy —— 默认关闭的 oracle 必须真的关着（18 §2.4 / §2.5）。

写在文档里的 `enabled: false` 不是默认值，只有"启动期抛错"才是默认值。
本脚本按 profile 未显式启用的情形跑一次校验；任何写/并发类被放行即 exit 1。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from veripen_oracles import ALL_ORACLES, policy  # noqa: E402


def main() -> int:
    ids = [cls.meta.id for cls in ALL_ORACLES]
    decision = policy.check_enabled(ids)  # 没有任何 enabled，模拟"客户未授权"

    missing = sorted(set(policy.DEFAULT_OFF) - set(decision.blocked))
    if missing:
        print(f"lint-policy: FAIL 默认关闭失效：{missing}", file=sys.stderr)
        return 1

    # 显式开启后仍需顶棚：reversible_write 在只读顶棚下必须继续被拒
    impact = {oid: cls.meta.impact for cls, oid in ((c, c.meta.id) for c in ALL_ORACLES)}
    opened = policy.check_enabled(
        ["mass_assignment"], enabled=["mass_assignment"], impact_by_oracle=impact
    )
    if "impact_exceeds_ceiling" not in opened.blocked.values():
        print("lint-policy: FAIL 开启后未受 max_impact_class 约束", file=sys.stderr)
        return 1

    print(f"lint-policy: OK 默认关闭 {len(policy.DEFAULT_OFF)} 个；开启后仍受顶棚约束")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
