"""B312 · 演示档必须能跑通回环靶场。

B311 让 `DENY_SET_DEFAULT` 真的生效（展开含回环/私网），于是写这个关键字的演示档
在回环 lab 上变成"0 验证 / 0 证据"——实测过。演示档改用只挡云元数据的口径，
生产档仍用 `DENY_SET_DEFAULT`。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from veripen_core.profile import load_profile

ROOT = Path(__file__).resolve().parents[2]
PROFILES = sorted((ROOT / "profiles").glob("*/profile.yaml"))


def test_there_are_demo_profiles() -> None:
    assert PROFILES, "profiles/ 下没有演示档"


@pytest.mark.parametrize("path", PROFILES, ids=[p.parent.name for p in PROFILES])
def test_demo_profile_does_not_deny_loopback(path: Path) -> None:
    prof = load_profile(path)
    assert "DENY_SET_DEFAULT" not in prof.deny, (
        "演示档写 DENY_SET_DEFAULT 会把回环靶场整段拒掉（0 验证 0 证据）")
    assert prof.deny, "演示档也该有黑名单，别留空"
