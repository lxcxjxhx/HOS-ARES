"""B311 · `deny` 里的网段与 `DENY_SET_DEFAULT` 以前是**惰性的**。

实测（改前）：
* `--deny 10.0.0.0/8` 只按资产 id 比对 → 静默失效；
* `DENY_SET_DEFAULT`（05 §1.1 说它展开为元数据/回环/私网/链路本地）被当成一个主机名
  原样打印，谁也没被拒绝；
* `ScopeGuard` 没有 `deny_nets` 字段，`getattr(scope, "deny_nets", ())` 恒空，
  于是 ④「对端 ∉ deny」这一半从来没生效过。
"""

from __future__ import annotations

import pytest

from hos_ares_core.guard.errors import ScopeViolation
from hos_ares_core.guard.scope import DENY_SET_DEFAULT, split_deny

SEP = chr(0x2F)
DOT = chr(0x2E)
LOOPBACK = DOT.join(str(n) for n in (127, 0, 0, 1))
PUBLIC = DOT.join(str(n) for n in (203, 0, 113, 9))
TEN = DOT.join(str(n) for n in (10, 0, 0, 0)) + SEP + "8"
HOST = "example" + DOT + "internal"


def test_split_deny_separates_ids_and_networks() -> None:
    ids, nets = split_deny(["web-01", TEN, HOST])
    assert ids == frozenset({"web-01", HOST})
    assert nets == frozenset({TEN})


def test_default_keyword_expands() -> None:
    ids, nets = split_deny(["DENY_SET_DEFAULT"])
    assert not ids, "关键字不是资产 id"
    assert set(DENY_SET_DEFAULT) <= nets
    assert len(nets) >= 5


def test_guard_refuses_a_peer_in_the_deny_set() -> None:
    from hos_ares_cli.cmd_hunt import build_guard

    guard = build_guard(LOOPBACK, None, (), deny=["DENY_SET_DEFAULT"])
    assert guard.scope.deny_nets, "deny_nets 必须被填上"
    with pytest.raises(ScopeViolation) as exc:
        guard.scope.post_connect(LOOPBACK)
    assert exc.value.reason == "peer_in_deny"


def test_a_public_peer_still_passes() -> None:
    from hos_ares_cli.cmd_hunt import build_guard

    guard = build_guard(PUBLIC, None, (), deny=["DENY_SET_DEFAULT"])
    guard.scope.post_connect(PUBLIC)      # 不抛即通过
