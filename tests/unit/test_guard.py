"""B3 guard 测试：scope 四处 / 令牌桶 / 审批门 / 出网闸（05 §1–§3、04 §5）。"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from veripen_core.guard import (
    ApprovalGate,
    ApprovalRequired,
    ApprovalState,
    ApprovalTTLExpired,
    AssetScope,
    Budget,
    BudgetExhausted,
    NoApprovalGate,
    ScopeDenied,
    ScopeViolation,
    ThrottleConfig,
    ThrottleTimeout,
    TokenBucket,
    check_plan,
    check_post_connect,
)
from veripen_core.routes.types import RouteStale, RouteTooWeak, UnboundRoute
from veripen_core.guard.scope import ScopeGuard
from veripen_core.models import ImpactClass
from veripen_core.routes import (
    AssetRef,
    Resolver,
    RouteHints,
    RouteProvenance,
    RouteQuery,
    StaticSource,
)
from veripen_core.routes.types import RouteCandidate
from veripen_core.tools import http

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = __import__("json").loads(
    (ROOT / "tests" / "routes" / "fixtures" / "templates.json").read_text(encoding="utf-8")
)

NOW = 1_000


def _routeset(confidence_source: RouteProvenance = RouteProvenance.CONTRACT):
    cand = RouteCandidate(
        method="GET",
        template=TEMPLATES["user_by_id"],
        param_names=("id",),
        source=confidence_source,
    )
    q = RouteQuery(asset=AssetRef(id="web-01"), hints=RouteHints(semantic_tag="object_read"))
    return Resolver(sources=[StaticSource(confidence_source.value, confidence_source, [cand])]).resolve(q, now=NOW)


def _guard(scope_in=frozenset({"web-01"}), deny=frozenset(), asset_id="web-01") -> ScopeGuard:
    return ScopeGuard(
        asset=AssetScope(id=asset_id, resolved_ips=frozenset({"203.0.113.10"})),
        scope_in=scope_in,
        deny=deny,
        min_confidence=0.70,
        now=NOW,
    )


# ── ① 规划期 ────────────────────────────────────────────────────────
def test_plan_denies_asset_outside_scope() -> None:
    decision = check_plan(
        asset_id="web-09",
        scope_in={"web-01"},
        deny=set(),
        oracle_ids=["idor_cross_account"],
        enabled_oracles={"idor_cross_account"},
        impact_by_oracle={},
        asset_ceiling=ImpactClass.READ_ONLY,
        in_window=True,
    )
    assert decision.allowed_oracles == ()
    assert decision.blocked == {"idor_cross_account": "asset_not_in_scope"}


def test_plan_denies_deny_list_and_window() -> None:
    common = dict(
        scope_in={"web-01"},
        oracle_ids=["idor_cross_account"],
        enabled_oracles={"idor_cross_account"},
        impact_by_oracle={},
        asset_ceiling=ImpactClass.READ_ONLY,
    )
    denied = check_plan(asset_id="web-01", deny={"web-01"}, in_window=True, **common)
    assert denied.blocked == {"idor_cross_account": "asset_not_in_scope"}

    outside = check_plan(asset_id="web-01", deny=set(), in_window=False, **common)
    assert outside.blocked == {"idor_cross_account": "outside_test_window"}


def test_plan_returns_allowed_set_not_bool() -> None:
    decision = check_plan(
        asset_id="web-01",
        scope_in={"web-01"},
        deny=set(),
        oracle_ids=["a", "b", "c"],
        enabled_oracles={"a", "c"},
        impact_by_oracle={"c": ImpactClass.REVERSIBLE_WRITE},
        asset_ceiling=ImpactClass.READ_ONLY,
        in_window=True,
    )
    assert decision.allowed_oracles == ("a",)
    assert decision.blocked == {"b": "oracle_disabled", "c": "impact_exceeds_ceiling"}


# ── ② 绑定门 ────────────────────────────────────────────────────────
def test_binding_ok() -> None:
    rs = _routeset()
    guard = _guard()
    binding = guard.binding(rs.bindings[0].ref, rs)
    assert binding.ref == rs.bindings[0].ref


def test_unbound_ref_is_violation() -> None:
    rs = _routeset()
    guard = _guard()
    with pytest.raises(UnboundRoute):
        guard.binding("route://contract/0000000000000000", rs)
    # 来源越界要留痕（03 §7 第 11 条查它）
    assert any(
        e["kind"] == "binding" and e["ok"] is False and e["detail"].get("VIOLATION") == "UNBOUND"
        for e in guard.events
    )


def test_weak_binding_denied() -> None:
    rs = _routeset(RouteProvenance.PROBE)  # 0.50 < 0.70
    with pytest.raises(RouteTooWeak):
        _guard().binding(rs.bindings[0].ref, rs)


def test_expired_binding_denied() -> None:
    rs = _routeset()
    late = ScopeGuard(
        asset=AssetScope(id="web-01"),
        scope_in={"web-01"},
        deny=set(),
        min_confidence=0.70,
        now=rs.expires_at + 1,
    )
    with pytest.raises(RouteStale):
        late.binding(rs.bindings[0].ref, rs)


# ── ③ 预算 ──────────────────────────────────────────────────────────
def test_budget_hard_stop() -> None:
    budget = Budget(requests=2)
    budget.spend_request()
    budget.spend_request()
    assert budget.exhausted
    with pytest.raises(BudgetExhausted):
        budget.spend_request()


# ── ④ 连接后 ────────────────────────────────────────────────────────
def test_post_connect_allows_resolved_ip() -> None:
    asset = AssetScope(id="web-01", resolved_ips=frozenset({"203.0.113.10"}))
    check_post_connect(peer_ip="203.0.113.10", asset=asset, deny_nets=["10.0.0.0/8"])


def test_post_connect_denies_private_and_unknown() -> None:
    asset = AssetScope(id="web-01", resolved_ips=frozenset({"203.0.113.10"}))
    with pytest.raises(ScopeViolation) as info:
        check_post_connect(peer_ip="10.1.2.3", asset=asset, deny_nets=["10.0.0.0/8"])
    assert info.value.reason == "peer_in_deny"

    with pytest.raises(ScopeViolation):
        check_post_connect(peer_ip="198.51.100.7", asset=asset, deny_nets=[])


def test_post_connect_normalizes_ipv4_mapped() -> None:
    asset = AssetScope(id="web-01", resolved_ips=frozenset({"203.0.113.10"}))
    check_post_connect(peer_ip="::ffff:203.0.113.10", asset=asset, deny_nets=[])


def test_post_connect_violation_is_recorded() -> None:
    guard = _guard()
    with pytest.raises(ScopeViolation):
        guard.post_connect("10.0.0.5", deny_nets=["10.0.0.0/8"])
    assert guard.violations()[0]["detail"]["VIOLATION"] == "peer_in_deny"


# ── 令牌桶 ──────────────────────────────────────────────────────────
class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_bucket_burst_then_refill() -> None:
    clock = FakeClock()
    bucket = TokenBucket(ThrottleConfig(rate_per_s=20, burst=40, max_wait_s=0.1), clock=clock)
    assert all(bucket.try_acquire() for _ in range(40))
    assert bucket.try_acquire() is False
    clock.t = 1.0
    assert all(bucket.try_acquire() for _ in range(20))
    assert bucket.try_acquire() is False


def test_bucket_throttle_timeout() -> None:
    cfg = ThrottleConfig(rate_per_s=1, burst=1, max_wait_s=0.2, poll_s=0.01)
    bucket = TokenBucket(cfg)
    bucket.acquire()
    with pytest.raises(ThrottleTimeout):
        bucket.acquire()


def test_bucket_throughput_about_20_per_s() -> None:
    cfg = ThrottleConfig(rate_per_s=20, burst=1, max_wait_s=5, poll_s=0.005)
    bucket = TokenBucket(cfg)
    passed = 0
    started = time.monotonic()
    lock = threading.Lock()

    def worker() -> None:
        nonlocal passed
        while time.monotonic() - started < 1.0:
            if bucket.try_acquire():
                with lock:
                    passed += 1
            time.sleep(0.001)

    threads = [threading.Thread(target=worker) for _ in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    elapsed = time.monotonic() - started
    # 100 并发下通过数≈20×时长。
    # 容差 30% → 45%：CI/Windows 上线程调度抖动会让单次采样差 30% 以上；
    # 这条测的是"量级对不对"（不是 2/s 也不是 200/s），精度交给 bench（12）。
    expected = 20 * elapsed
    assert abs(passed - expected) / expected < 0.45, (passed, round(elapsed, 3))


# ── 审批门 ──────────────────────────────────────────────────────────
def test_no_gate_means_denied() -> None:
    gate = ApprovalGate.from_spec([])
    with pytest.raises(NoApprovalGate):
        gate.require("scope_expand", now=NOW)


def test_require_then_approve_then_pass() -> None:
    gate = ApprovalGate.from_spec([{"action_class": "scope_expand"}])
    with pytest.raises(ApprovalRequired):
        gate.require("scope_expand", now=NOW)
    assert gate.decisions[-1].state is ApprovalState.PENDING

    gate.decide("scope_expand", approved=True, by="human", lease_owner="worker-1", now=NOW + 10)
    gate.require("scope_expand", now=NOW + 11)  # first_time：本 engagement 批过即放行


def test_cannot_self_approve() -> None:
    gate = ApprovalGate.from_spec([{"action_class": "scope_expand"}])
    with pytest.raises(ApprovalRequired):
        gate.require("scope_expand", now=NOW)
    with pytest.raises(PermissionError):
        gate.decide("scope_expand", approved=True, by="worker-1", lease_owner="worker-1", now=NOW)


def test_rejected_stays_denied() -> None:
    gate = ApprovalGate.from_spec([{"action_class": "discovery_probe"}])
    with pytest.raises(ApprovalRequired):
        gate.require("discovery_probe", now=NOW)
    gate.decide("discovery_probe", approved=False, by="human", lease_owner="w1", now=NOW)
    with pytest.raises(ApprovalRequired):
        gate.require("discovery_probe", now=NOW + 1)


def test_approval_ttl_expires() -> None:
    gate = ApprovalGate.from_spec([{"action_class": "scope_expand"}], ttl_s=60)
    with pytest.raises(ApprovalRequired):
        gate.require("scope_expand", now=NOW)
    with pytest.raises(ApprovalTTLExpired):
        gate.decide("scope_expand", approved=True, by="human", lease_owner="w1", now=NOW + 61)
    assert gate.decisions[-1].state is ApprovalState.EXPIRED


# ── 出网闸（05 §1.0 硬约束 1）────────────────────────────────────────
@pytest.fixture()
def installed():
    rs = _routeset()
    guard = _guard()
    plan = guard.plan(
        ["idor_cross_account"],
        enabled_oracles={"idor_cross_account"},
        impact_by_oracle={},
        asset_ceiling=ImpactClass.READ_ONLY,
        in_window=True,
    )
    ctx = __import__("veripen_core.guard.context", fromlist=["GuardContext"]).GuardContext(
        scope=guard,
        routeset=rs,
        plan=plan,
        oracle_id="idor_cross_account",
        now=NOW,
    )
    http.install_guard(ctx)
    yield ctx, rs
    http.uninstall_guard()


def test_send_without_guard_is_refused() -> None:
    rs = _routeset()
    http.uninstall_guard()
    with pytest.raises(Exception) as info:
        http.send(rs.bindings[0], {})
    assert type(info.value).__name__ == "GuardNotInstalled"


def test_send_runs_binding_gate_then_transport(installed) -> None:
    ctx, rs = installed
    before = len(ctx.scope.events)
    with pytest.raises(http.TransportUnavailable):
        http.send(rs.bindings[0], {})
    assert len(ctx.scope.events) >= before
    assert ctx.budget.requests.used == 1  # preflight 真的跑了


def test_send_denies_out_of_plan_oracle(installed) -> None:
    ctx, rs = installed
    ctx.oracle_id = "not_in_plan"
    with pytest.raises(ScopeDenied):
        http.send(rs.bindings[0], {})


def test_send_denies_unbound_ref_and_records(installed) -> None:
    ctx, rs = installed
    alien = rs.bindings[0].__class__(
        **{**rs.bindings[0].__dict__, "ref": "route://contract/ffffffffffffffff"}
    )
    with pytest.raises(UnboundRoute):
        http.send(alien, {})
    assert any(e["ok"] is False for e in ctx.scope.events)
