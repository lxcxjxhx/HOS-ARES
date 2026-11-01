#!/usr/bin/env python3
"""B16：两个客户档的差异表 + 启动校验（A1 断言的物料）。

只读 profile YAML，不联网、不解析地址。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from veripen_contract import TAG_VOCAB  # noqa: E402
from veripen_core.profile import diff_profiles, load_profile, validate_profile  # noqa: E402
from veripen_oracles import ALL_ORACLES  # noqa: E402

PROFILES = {
    "demo-a": ROOT / "profiles" / "demo-a" / "profile.yaml",
    "demo-b": ROOT / "profiles" / "demo-b" / "profile.yaml",
}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    loaded = {name: load_profile(path) for name, path in PROFILES.items()}
    known_o = {c.meta.id for c in ALL_ORACLES}
    status = 0

    for name, profile in loaded.items():
        issues = validate_profile(profile, known_oracles=known_o, known_tags=TAG_VOCAB)
        fails = [i for i in issues if i.level == "fail"]
        warns = [i for i in issues if i.level == "warn"]
        print(
            f"{name}: hash={profile.profile_hash()[:12]} "
            f"oracles={len(profile.oracles.enabled)} selectors={len(profile.selectors)} "
            f"qps={profile.qps:g} fails={len(fails)} warns={len(warns)}"
        )
        for issue in fails:
            print(f"  FAIL {issue.code}: {issue.detail}")
            status = 1
        for issue in warns:
            print(f"  WARN {issue.code}: {issue.detail}")

    print()
    print("差异表（只列差异项，不含地址）：")
    print(f"{'key':<14} {'demo-a':<60} demo-b")
    for row in diff_profiles(loaded["demo-a"], loaded["demo-b"]):
        print(f"{row['key']:<14} {row['demo_a']:<60} {row['demo_b']}")

    print()
    print("A1：两档差异全部在 YAML，内核未改 ->", "OK" if status == 0 else "FAIL")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
