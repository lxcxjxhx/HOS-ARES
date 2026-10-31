"""B4 六态 replay 与**判定顺序**回归（06 §5.1.1 / §8 断言 8）。

三条顺序断言（打乱即失败）：
1. 端点下线 → ``ROUTE_UNRESOLVED``（**不得**是 FAILED）
2. 模板变更 → ``ROUTE_DRIFT``
3. 目标整体"任何 token 都 200" → ``FAILED``（**不得**是 OK）
"""

from __future__ import annotations

import hashlib

import capsule_fixture as F
import pytest

from veripen_core.evidence import (
    ReplayBlocked,
    ReplayEnv,
    ReplayMode,
    TraceResult,
    replay,
)
from veripen_core.models import ReplayStatus


class ScriptedTransport:
    """按 role 回放预设 trace；记录实际发包次数（用于证明"该不发的没发"）。"""

    def __init__(self, by_role: dict[str, TraceResult], default: TraceResult | None = None) -> None:
        self.by_role = by_role
        self.default = default
        self.calls: list[tuple[int, str]] = []

    def send(self, step, binding):  # type: ignore[no-untyped-def]
        self.calls.append((step.n, step.role))
        assert binding.ref == step.route_ref, "replay 必须用重解析出的同一条绑定"
        return self.by_role.get(step.role) or self.default or TraceResult(
            status=step.response.status, body_sha256=step.response.body_sha256
        )


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _live_env(rs=None, **kw) -> ReplayEnv:
    return ReplayEnv(now=F.NOW, mode=ReplayMode.LIVE, routeset=rs, **kw)


def _matching(rs=None) -> dict[str, TraceResult]:
    cap = F.capsule()
    return {
        s.role: TraceResult(status=s.response.status, body_sha256=s.response.body_sha256)
        for s in cap.steps
    }


# ── 6 态各一条 ─────────────────────────────────────────────────────
def test_ok() -> None:
    rs = F.routeset()
    t = ScriptedTransport(_matching())
    result = replay(F.sealed(), _live_env(rs), t)
    assert result.state is ReplayStatus.OK
    assert [c[1] for c in t.calls] == ["baseline", "probe", "control"], "三步含对照都要重发"


def test_partial_when_body_changed_but_conclusion_holds() -> None:
    rs = F.routeset()
    traces = _matching()
    traces["probe"] = TraceResult(status=200, body_sha256=_sha("same-json-different-bytes"))
    result = replay(F.sealed(), _live_env(rs), ScriptedTransport(traces))
    assert result.state is ReplayStatus.PARTIAL
    assert result.reason == "body_or_status_changed"


def test_failed_when_assertion_breaks() -> None:
    rs = F.routeset()
    traces = _matching()
    traces["control"] = TraceResult(status=200, body_sha256=_sha("now-everything-works"))
    result = replay(F.sealed(), _live_env(rs), ScriptedTransport(traces))
    assert result.state is ReplayStatus.FAILED
    assert result.reason == "assertion_not_reproduced"


def test_blocked_when_scope_shrinks_and_zero_requests() -> None:
    rs = F.routeset()
    t = ScriptedTransport(_matching())
    env = _live_env(rs, scope_in=frozenset({"web-02"}))
    result = replay(F.sealed(), env, t)
    assert result.state is ReplayStatus.BLOCKED
    assert result.reason == "asset_out_of_scope"
    assert t.calls == [], "不该测的时候一个包都不能发"


def test_blocked_when_plan_denied() -> None:
    rs = F.routeset()
    t = ScriptedTransport(_matching())
    result = replay(F.sealed(), _live_env(rs, plan_allowed=False), t)
    assert result.state is ReplayStatus.BLOCKED
    assert result.reason == "plan_denied"
    assert t.calls == []


def test_route_unresolved_when_binding_deleted() -> None:
    """DoD：删绑定 → ROUTE_UNRESOLVED（不是"已修复"）。"""
    rs = F.routeset()
    t = ScriptedTransport(_matching())
    result = replay(F.sealed(), _live_env(F.drop_binding(rs)), t)
    assert result.state is ReplayStatus.ROUTE_UNRESOLVED
    assert result.reason == "route_ref_not_resolvable"
    assert t.calls == []


def test_route_drift_when_template_changed() -> None:
    """DoD：改模板 → ROUTE_DRIFT（不是"未复现"）。"""
    rs = F.routeset()
    ref = F.first_ref(rs)
    drifted = F.clone_with_template(rs, ref, F.TEMPLATES["items"], "b" * 64)
    t = ScriptedTransport(_matching())
    result = replay(F.sealed(), _live_env(drifted), t)
    assert result.state is ReplayStatus.ROUTE_DRIFT
    assert result.reason == "template_sha256_changed"
    assert t.calls == []


# ── 顺序不可交换（三条各一个反例）─────────────────────────────────
def test_order_route_before_resend() -> None:
    """端点下线时若先重发：404 → FAILED → 报告写"已修复"。必须先判路由。"""
    rs = F.routeset()
    t = ScriptedTransport({}, default=TraceResult(status=404, body_sha256=_sha("gone")))
    result = replay(F.sealed(), _live_env(F.drop_binding(rs)), t)
    assert result.state is ReplayStatus.ROUTE_UNRESOLVED
    assert result.state is not ReplayStatus.FAILED
    assert t.calls == []


def test_order_scope_before_everything() -> None:
    """不该测的时候连路由都不解析、更不发包。"""
    rs = F.routeset()
    t = ScriptedTransport({}, default=TraceResult(status=500, body_sha256=""))
    result = replay(F.sealed(), _live_env(F.drop_binding(rs), scope_in=frozenset()), t)
    assert result.state is ReplayStatus.BLOCKED, "授权现状优先于路由现状"
    assert t.calls == []


def test_order_three_conditions_before_details() -> None:
    """三条件不成立时细节"恰好一样"也不许判 OK（防假确认）。"""
    rs = F.routeset()
    cap = F.sealed()
    traces = _matching()
    traces["control"] = TraceResult(status=200, body_sha256=cap.steps[2].response.body_sha256)
    result = replay(cap, _live_env(rs), ScriptedTransport(traces))
    assert result.state is ReplayStatus.FAILED


# ── 完整性最先查（与网络无关）──────────────────────────────────────
def test_tampered_capsule_never_reaches_network() -> None:
    from veripen_core.evidence import CapsuleTampered

    cap = F.sealed()
    forged = cap.model_copy(update={"confidence": 0.5})
    t = ScriptedTransport(_matching())
    with pytest.raises(CapsuleTampered):
        replay(forged, _live_env(F.routeset()), t)
    assert t.calls == []


def test_live_without_transport_refuses() -> None:
    with pytest.raises(ReplayBlocked):
        replay(F.sealed(), _live_env(F.routeset()), None)


def test_live_without_routeset_refuses() -> None:
    with pytest.raises(ReplayBlocked):
        replay(F.sealed(), _live_env(None), ScriptedTransport(_matching()))


# ── dry-run：只跑门，不发包 ────────────────────────────────────────
def test_dry_run_lists_would_send_without_sending() -> None:
    t = ScriptedTransport(_matching())
    env = ReplayEnv(now=F.NOW, mode=ReplayMode.DRY, routeset=F.routeset())
    result = replay(F.sealed(), env, t)
    assert result.state is ReplayStatus.NEVER
    assert result.reason == "dry_run"
    assert [x[1] for x in result.would_send] == ["baseline", "probe", "control"]
    assert t.calls == []


def test_dry_run_still_reports_route_gate() -> None:
    env = ReplayEnv(now=F.NOW, mode=ReplayMode.DRY, routeset=F.drop_binding(F.routeset()))
    result = replay(F.sealed(), env, None)
    assert result.state is ReplayStatus.ROUTE_UNRESOLVED


# ── 语料额外：profile 变了不阻断，只标 stale（06 §5 步骤 2）────────
def test_profile_changed_marks_stale_but_continues() -> None:
    rs = F.routeset()
    result = replay(F.sealed(), _live_env(rs, profile_ok=False), ScriptedTransport(_matching()))
    assert result.state is ReplayStatus.OK
    assert result.stale is True
