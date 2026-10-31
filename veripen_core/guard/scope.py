"""scope 四处校验（05 §1）。

| 处 | 时机 | 函数 |
|---|---|---|
| ① plan | 领任务后一次 | ``check_plan`` → ``PlanDecision``（**产物是允许集合，不是布尔**）|
| ② binding | 每次请求 | ``check_binding`` |
| ③ preflight | 每次请求 | ``check_preflight``（复核 ①+②，再查预算与令牌）|
| ④ post_connect | 每个连接/每次跳转 | ``check_post_connect``（断连 + 越界事件）|

④ 的实现只能挂在 transport 层：应用层拿不到“实际连了谁”（05 §1.0 硬约束 2）。
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from typing import AbstractSet, Iterable

from ..routes.types import (
    RouteBinding,
    RouteError,
    RouteSet,
    RouteStale,
    RouteTooWeak,
    UnboundRoute,
)
from .errors import ScopeDenied, ScopeViolation
from ..models import ImpactClass


# ── 资产范围 ────────────────────────────────────────────────────────
@dataclass(frozen=True)
class AssetScope:
    """一个资产的许可面：值（endpoint）不在这里，只有 id 与解析集合。"""

    id: str
    criticality: str = "medium"
    impact_ceiling: ImpactClass = ImpactClass.READ_ONLY
    host_keys: frozenset[str] = field(default_factory=frozenset)
    resolved_ips: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class GuardDecision:
    """①②③ 的统一结论：允许 / 拒绝 / 原因。"""

    allowed: bool
    reason: str = "ok"
    code: str = ""

    def raise_if_denied(self) -> None:
        if not self.allowed:
            raise ScopeDenied(self.reason)


@dataclass(frozen=True)
class PlanDecision:
    """① 的产物：允许的 oracle 集合 + 每个被挡者的原因（报告要能写“未覆盖”）。"""

    allowed_oracles: tuple[str, ...]
    blocked: dict[str, str] = field(default_factory=dict)

    def is_allowed(self, oracle_id: str) -> bool:
        return oracle_id in self.allowed_oracles


# ── ① 规划期 ────────────────────────────────────────────────────────
def check_plan(
    *,
    asset_id: str,
    scope_in: AbstractSet[str],
    deny: AbstractSet[str],
    oracle_ids: Iterable[str],
    enabled_oracles: AbstractSet[str],
    impact_by_oracle: dict[str, ImpactClass],
    asset_ceiling: ImpactClass,
    in_window: bool,
) -> PlanDecision:
    """不通过 → 该 oracle 标 BLOCKED，**不发包**；返回集合而不是抛异常。"""
    allowed: list[str] = []
    blocked: dict[str, str] = {}

    if asset_id in deny or asset_id not in scope_in:
        # 资产本身越界：整任务全挡
        return PlanDecision((), {o: "asset_not_in_scope" for o in oracle_ids})
    if not in_window:
        return PlanDecision((), {o: "outside_test_window" for o in oracle_ids})

    ceiling_rank = _rank(asset_ceiling)
    for oid in oracle_ids:
        if oid not in enabled_oracles:
            blocked[oid] = "oracle_disabled"
            continue
        impact = impact_by_oracle.get(oid, ImpactClass.READ_ONLY)
        if _rank(impact) > ceiling_rank:
            blocked[oid] = "impact_exceeds_ceiling"
            continue
        allowed.append(oid)
    return PlanDecision(tuple(allowed), blocked)


def _rank(impact: ImpactClass) -> int:
    return {ImpactClass.READ_ONLY: 0, ImpactClass.REVERSIBLE_WRITE: 1}[impact]


# ── ② 绑定门 ────────────────────────────────────────────────────────
def check_binding(
    *,
    ref: str,
    routeset: RouteSet,
    min_confidence: float,
    now: int,
    asset_host_keys: AbstractSet[str],
) -> RouteBinding:
    """唯一一处能回答“这个请求的地址是从哪来的”（05 §1.0.2 来源越界）。"""
    binding = next((b for b in routeset.bindings if b.ref == ref), None)
    if binding is None:
        raise UnboundRoute(ref)
    if binding.confidence < min_confidence:
        raise RouteTooWeak(ref)
    if now >= routeset.expires_at:
        raise RouteStale(ref)
    if asset_host_keys and binding.template and not _host_registered(binding, asset_host_keys):
        raise ScopeDenied("binding_host_not_registered", ref=ref)
    return binding


def _host_registered(binding: RouteBinding, asset_host_keys: AbstractSet[str]) -> bool:
    """模板本身不含 host 时视为已登记；含 host 时必须在登记集合内。

    模板来自目标契约，可能带绝对前缀；这里只做“是否已登记”的集合判断，
    不解析、不归一成地址。
    """
    template = binding.template
    for key in asset_host_keys:
        if key and key in template:
            return True
    return not template.startswith("http")


# ── ③ 执行前 ────────────────────────────────────────────────────────
def check_preflight(
    *,
    plan: PlanDecision,
    oracle_id: str,
    binding_ok: bool,
    budget_ok: bool,
    throttle_ok: bool,
    approval_ok: bool,
) -> GuardDecision:
    """复核 ① + ② + 预算 + 令牌 + 审批。任一不过即拒绝（**不跳过**）。"""
    if not plan.is_allowed(oracle_id):
        return GuardDecision(False, "plan_denied", ScopeDenied.code)
    if not binding_ok:
        return GuardDecision(False, "binding_denied", ScopeDenied.code)
    if not budget_ok:
        return GuardDecision(False, "budget_exhausted", "VP-3002")
    if not throttle_ok:
        return GuardDecision(False, "throttle_unavailable", "VP-3003")
    if not approval_ok:
        return GuardDecision(False, "approval_missing", "VP-2005")
    return GuardDecision(True)


# ── ④ 连接后 ────────────────────────────────────────────────────────
def check_post_connect(
    *,
    peer_ip: str,
    asset: AssetScope,
    deny_nets: Iterable[str],
    explicit_ips: Iterable[str] = (),
    stop_on_violation: bool = True,
) -> None:
    """实际对端必须 ∈ 解析集合且 ∉ deny；否则断连 + 越界事件（P0）。"""
    ip = _normalize(peer_ip)
    allowed = {_normalize(x) for x in (set(asset.resolved_ips) | set(explicit_ips))}
    denied = [ipaddress.ip_network(n, strict=False) for n in deny_nets]

    if any(ip in net for net in denied):
        raise ScopeViolation(str(ip), asset.id, reason="peer_in_deny")
    if ip not in allowed:
        raise ScopeViolation(str(ip), asset.id)

    del stop_on_violation  # 语义：违反即抛，停不停任务由调用方按 policy 决定


def _normalize(text: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """IPv4-mapped IPv6 / 十进制 IP 一律归一（05 §1.1）。"""
    addr = ipaddress.ip_address(text)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    return addr


class ScopeGuard:
    """把四处校验绑成一个对象：worker 每任务一个，测试可直接驱动。"""

    def __init__(
        self,
        *,
        asset: AssetScope,
        scope_in: AbstractSet[str],
        deny: AbstractSet[str],
        min_confidence: float,
        now: int,
    ) -> None:
        self.asset = asset
        self.scope_in = scope_in
        self.deny = deny
        self.min_confidence = min_confidence
        self.now = now
        self.events: list[dict[str, object]] = []

    def plan(self, oracle_ids: Iterable[str], **kw: object) -> PlanDecision:
        decision = check_plan(
            asset_id=self.asset.id,
            scope_in=self.scope_in,
            deny=self.deny,
            oracle_ids=oracle_ids,
            **kw,  # type: ignore[arg-type]
        )
        self._record("scope", ok=bool(decision.allowed_oracles), detail={"plan_blocked": decision.blocked})
        return decision

    def binding(self, ref: str, routeset: RouteSet) -> RouteBinding:
        try:
            return check_binding(
                ref=ref,
                routeset=routeset,
                min_confidence=self.min_confidence,
                now=self.now,
                asset_host_keys=self.asset.host_keys,
            )
        except UnboundRoute as exc:
            # 来源越界是 P0 前兆：必须留痕，03 §7 第 11 条会查它
            self._record("binding", ok=False, detail={"VIOLATION": "UNBOUND", "ref": ref})
            raise exc
        except RouteError as exc:  # 路由层错误统一转 guard 错误
            self._record("binding", ok=False, detail={"reason": type(exc).__name__})
            raise

    def post_connect(self, peer_ip: str, deny_nets: Iterable[str], explicit_ips: Iterable[str] = ()) -> None:
        try:
            check_post_connect(
                peer_ip=peer_ip,
                asset=self.asset,
                deny_nets=deny_nets,
                explicit_ips=explicit_ips,
            )
        except ScopeViolation as exc:
            self._record(
                "scope",
                ok=False,
                detail={"VIOLATION": exc.reason, "ip": exc.peer_ip, "asset": exc.asset_id},
            )
            raise

    def _record(self, kind: str, ok: bool, detail: dict[str, object]) -> None:
        self.events.append({"kind": kind, "ok": ok, "detail": detail})

    def violations(self) -> list[dict[str, object]]:
        return [e for e in self.events if e["ok"] is False]
