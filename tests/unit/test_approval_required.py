"""B188：写类 oracle 抬了顶棚就能跑，一条审批都不留。

判据：
* `GuardContext.approval_action` 在产品代码里**从没有人设过**（只有 dataclass 默认 None）；
* 于是 `preflight()` 里那段审批检查永远不触发。

B187 把影响顶棚变成真的以后，`--max-impact reversible_write` 就成了"合法放行写类"，
而审批门形同不存在——抬个开关就能对目标做可逆写操作，审计里啥也没有。

现在的口径：**审批按影响级别自动生效**。写类必须有门；没有门就拒发（fail-closed），
理由写 `NoApprovalGate: reversible_write`，读者一看就知道缺什么。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))


def _required(oracle_id: str) -> str | None:
    from veripen_oracles.policy import approval_for

    return approval_for(oracle_id)


def test_write_oracle_requires_approval() -> None:
    assert _required("mass_assignment") == "reversible_write"
    assert _required("xss_stored_probe") == "reversible_write"


def test_read_only_oracles_need_nothing() -> None:
    for oid in ("sqli_bool_diff", "csp_absent", "idor_cross_account"):
        assert _required(oid) is None, oid


def test_unknown_oracle_is_not_gated() -> None:
    assert _required("") is None
    assert _required("no_such_oracle") is None


def _ctx(oracle_id: str, approval: object | None = None):  # noqa: ANN202
    from veripen_core.guard.context import GuardContext
    from veripen_core.guard.scope import PlanDecision, ScopeGuard

    scope = ScopeGuard(
        asset=__import__("veripen_core.guard.scope", fromlist=["AssetScope"]).AssetScope(
            id="web-01", resolved_ips=frozenset({"203.0.113.10"})
        ),
        scope_in=frozenset({"web-01"}),
        deny=frozenset(),
        min_confidence=0.0,
        now=1_000,
    )
    from veripen_core.guard.budget import Budget
    from veripen_core.guard.ratelimit import ThrottleConfig, TokenBucket

    return GuardContext(
        scope=scope,
        routeset=None,
        plan=PlanDecision(allowed_oracles=(oracle_id,)),
        bucket=TokenBucket(ThrottleConfig(rate_per_s=1000.0, burst=1000.0)),
        budget=Budget(),
        now=1_000,
        oracle_id=oracle_id,
        approval=approval,
        approval_action=("reversible_write"
                         if _required(oracle_id) == "reversible_write" else None),
    )


def test_preflight_refuses_write_oracle_without_a_gate() -> None:
    from veripen_core.guard.errors import NoApprovalGate

    with pytest.raises(NoApprovalGate):
        _ctx("mass_assignment").preflight()


def test_preflight_allows_read_oracle_without_a_gate() -> None:
    _ctx("sqli_bool_diff").preflight()      # 不抛 = 通过


def test_preflight_passes_when_the_gate_approved() -> None:
    from veripen_core.guard.approval import ApprovalGate
    from veripen_core.store import Store

    store = Store(":memory:").init()
    try:
        gate = ApprovalGate.from_spec([{"action_class": "reversible_write"}],
                                      store=store, events=None, task_id="t1")
        from veripen_core.guard.approval import ApprovalRequired

        try:
            gate.require("reversible_write", now=1_000, impact={"assets": "1"})
        except ApprovalRequired:
            pass          # require 的第一下必然抛：这是"提出申请"
        gate.decide("reversible_write", approved=True, by="operator",
                    lease_owner="agent", now=1_001, reason="测试放行")
        _ctx("mass_assignment", approval=gate).preflight()   # 有门且已签 -> 通过
    finally:
        store.close()
