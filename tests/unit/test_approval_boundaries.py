"""B612 · 审批门的**边界**：过期之后不许"顺手放行"。

## 补的是什么

`test_expired_marked_and_recorded` 验了「过期后被批会 raise」和「落库为 EXPIRED」。
但它**没验**过期之后 `approved()` 返回什么 —— 而 `approved()` 才是
真正决定「这个动作能不能做」的那道判定。

如果过期只影响 `decide()` 的返回值、却没影响 `approved()`，
那就成了**标记过期但实际放行**：审计里写着 EXPIRED，动作照做。

## 判据

1. 过期后 `approved()` 必须是 `False`
2. 正常批准过的 `approved()` 必须是 `True`（不能因为本批收紧而误伤）
3. 过期不能被"重新申请"自动变回 PENDING 而不留痕
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests", ROOT / "tests" / "unit"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from hos_ares_core.guard.approval import (  # noqa: E402
    ApprovalGate, ApprovalRequired, ApprovalTTLExpired,
)
from hos_ares_core.blackboard import EventLog  # noqa: E402
from hos_ares_core.store import Store  # noqa: E402


def _gate(*, ttl_s: int = 60) -> ApprovalGate:
    store = Store(":memory:").init()
    return ApprovalGate.from_spec(
        [{"action_class": "scope_expand"}],
        ttl_s=ttl_s,
        store=store,
        events=EventLog(store),
        task_id="T-001",
    )


# ── 1：过期后不许放行 ──────────────────────────────────────
def test_expired_approval_does_not_grant_access() -> None:
    """**这是本批的核心判据。**

    只标记 EXPIRED 而不影响 `approved()`，等于「审计写着过期、动作照做」。
    """
    gate = _gate(ttl_s=60)
    with pytest.raises(ApprovalRequired):
        gate.require("scope_expand", now=1000)
    with pytest.raises(ApprovalTTLExpired):
        gate.decide("scope_expand", approved=True, by="human",
                    lease_owner="agent", now=2000)

    assert gate.approved("eng-1", "scope_expand") is False, (
        "过期了但 approved() 仍放行 —— 审计与执行不一致"
    )


# ── 2：正常批准仍要放行（不误伤）───────────────────────────
def test_approved_then_grants_access() -> None:
    gate = _gate(ttl_s=3600)
    with pytest.raises(ApprovalRequired):
        gate.require("scope_expand", now=1000)
    gate.decide("scope_expand", approved=True, by="human",
                lease_owner="agent", now=1100)
    assert gate.approved("eng-1", "scope_expand") is True


# ── 3：被拒绝的不放行 ──────────────────────────────────────
def test_rejected_does_not_grant_access() -> None:
    gate = _gate(ttl_s=3600)
    with pytest.raises(ApprovalRequired):
        gate.require("scope_expand", now=1000)
    gate.decide("scope_expand", approved=False, by="human",
                lease_owner="agent", now=1100)
    assert gate.approved("eng-1", "scope_expand") is False


# ── 4：没申请过的不放行（默认全拒）─────────────────────────
def test_never_requested_does_not_grant_access() -> None:
    """**默认全拒**：profile 没写 = 不允许，不是"等待审批"（VP-2005）。"""
    gate = _gate()
    assert gate.approved("eng-1", "scope_expand") is False


# ── 5：判据自身的前提 ──────────────────────────────────────
def test_the_gate_actually_tracks_state() -> None:
    """上面几条全靠 `approved()` 有区分力。它若恒 False，第 2 条会红。

    这条把「区分力」本身钉住 —— 与 `test_probe_would_catch_a_gate_that_allows_self_approval`
    同一思路：**先证明量具能分辨**。
    """
    gate = _gate(ttl_s=3600)
    with pytest.raises(ApprovalRequired):
        gate.require("scope_expand", now=1000)
    assert gate.approved("eng-1", "scope_expand") is False   # 待批 ≠ 已批
    gate.decide("scope_expand", approved=True, by="human",
                lease_owner="agent", now=1100)
    assert gate.approved("eng-1", "scope_expand") is True    # 批了就放
