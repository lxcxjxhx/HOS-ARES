"""B559 · 审批链路五条判据（重点：**自批要拒**、**拒绝也要留痕**）。

`guard/approval.py#ApprovalGate` 里有两条容易假绿的行为，本批专门钉住：

| 判据 | 为什么容易假绿 |
|---|---|
| 未批 → `ApprovalRequired` | 这条最容易验，也最容易只有它被测 |
| **自批 → 拒绝** | "谁签的字"与"谁提的单"是两件事；同一人时按批准处理会让审批变成摆设 |
| **拒绝也留痕** | 只记"批准过什么"，审计里就只剩"没批过"——看不出是没提还是被拒 |
| 批准后 `require` 放行且落库 | 光把内存状态改对不算 |
| 重复批准不产生两条 PENDING | 幂等；不测会看到"一单多批"的账 |

先写判据、再写量具——这个顺序在 B558 一次就抓出两个真缺陷。
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hos_ares_core.guard.approval import (  # noqa: E402
    ApprovalGate,
    ApprovalRequired,
    ApprovalState,
)
from hos_ares_core.store import Store  # noqa: E402

SPEC = [{"action_class": "scope_expand", "when": "first_time"}]


def _gate(**kw: Any) -> ApprovalGate:
    return ApprovalGate.from_spec(SPEC, **kw)


# ── 1：未批 → 阻断 ──────────────────────────────────────────
def test_unapproved_action_is_blocked() -> None:
    gate = _gate()
    with pytest.raises(ApprovalRequired):
        gate.require("scope_expand", now=100, impact={"assets": "multi"})
    pending = [d for d in gate.decisions if d.state is ApprovalState.PENDING]
    assert len(pending) == 1, gate.decisions


# ── 2：自批必须拒 ───────────────────────────────────────────
def test_self_approval_is_rejected() -> None:
    """**同一个人不能签自己的单。**

    如果自批被当成批准，审批就只是多一步点击。
    """
    gate = _gate()
    with pytest.raises(ApprovalRequired):
        gate.require("scope_expand", now=100)
    # **实际契约**：自批抛 `PermissionError`（不是返回一条 REJECTED 记录）。
    # 但它仍然要**留痕** —— 下面那半验证这一点。
    with pytest.raises(PermissionError, match="自我批准"):
        gate.decide("scope_expand", approved=True, by="alice",
                    lease_owner="alice", now=101)
    assert gate.approved("eng-1", "scope_expand") is False, "自批之后居然放行了"
    # 留痕：账上有一条 REJECTED，reason 指向 self_approval
    rejected = [d for d in gate.decisions if d.state is ApprovalState.REJECTED]
    assert rejected and rejected[-1].reason == "self_approval", gate.decisions


def test_another_person_can_approve() -> None:
    """换一个人签就该通过——否则上一道判据可能只是"永远拒"。"""
    gate = _gate()
    with pytest.raises(ApprovalRequired):
        gate.require("scope_expand", now=100)
    decision = gate.decide(
        "scope_expand", approved=True, by="bob", lease_owner="alice", now=101,
    )
    assert decision.state is ApprovalState.APPROVED, decision.state
    assert gate.approved("eng-1", "scope_expand") is True
    # 批过之后 require 不再抛
    gate.require("scope_expand", now=102)


def test_explicit_rejection_is_also_recorded() -> None:
    """**"不批准"与"没批过"必须在账上分开。**

    只看 `approved()` 的话两者都是 False，但审计里必须能区分
    "谁拒的、为什么拒"与"根本没人看"。
    """
    gate = _gate()
    with pytest.raises(ApprovalRequired):
        gate.require("scope_expand", now=100)
    decision = gate.decide(
        "scope_expand", approved=False, by="bob", lease_owner="alice", now=101,
        reason="风险太高",
    )
    assert decision.state is ApprovalState.REJECTED
    assert decision.decided_by == "bob"
    assert decision.reason == "风险太高", decision.reason
    rejected = [d for d in gate.decisions if d.state is ApprovalState.REJECTED]
    assert len(rejected) == 1, "拒绝没有留痕"


# ── 3：落库 ─────────────────────────────────────────────────
def test_decisions_are_persisted(tmp_path: Path) -> None:
    """批准要真的写进库：换一个连接能读回来。"""
    db = tmp_path / "a.db"
    store = Store(str(db)).init()
    try:
        gate = _gate(store=store, task_id="t-1")
        with pytest.raises(ApprovalRequired):
            gate.require("scope_expand", now=100, impact={"assets": "multi"})
        gate.decide("scope_expand", approved=True, by="bob",
                    lease_owner="alice", now=101)
    finally:
        store.close()

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT action_class, state, decided_by FROM approvals WHERE task_id='t-1'"
    ).fetchall()
    conn.close()
    assert rows, "审批决定没落库"
    assert rows[-1]["state"] == "APPROVED", dict(rows[-1])
    assert rows[-1]["decided_by"] == "bob"


# ── 4：幂等 ─────────────────────────────────────────────────
def test_approving_twice_does_not_stack_pending_records() -> None:
    """重复批准不该在账上留下两条 PENDING（那看起来像提了两次申请）。"""
    gate = _gate()
    with pytest.raises(ApprovalRequired):
        gate.require("scope_expand", now=100)
    gate.decide("scope_expand", approved=True, by="bob", lease_owner="alice", now=101)
    # **实际契约**：没有 PENDING 时再批抛 `KeyError`（不是静默新增一条）。
    # 这比"默默再记一条"好：账上不会出现"一单多批"。
    with pytest.raises(KeyError, match="没有待批记录"):
        gate.decide("scope_expand", approved=True, by="bob", lease_owner="alice", now=102)
    pending = [d for d in gate.decisions if d.state is ApprovalState.PENDING]
    assert not pending, f"重复批准留下了 {len(pending)} 条 PENDING"


def test_expired_approval_is_not_accepted() -> None:
    """**过期的单不能用**：TTL 到了要抛 `ApprovalTTLExpired`，且状态标 EXPIRED。

    这条不在原计划里，是写量具时读 `decide` 才看到的第三类收场——
    批准、拒绝、过期，三者对客户的下一步都不一样。
    """
    from hos_ares_core.guard.approval import ApprovalTTLExpired

    gate = _gate(ttl_s=10)
    with pytest.raises(ApprovalRequired):
        gate.require("scope_expand", now=100)
    with pytest.raises(ApprovalTTLExpired):
        gate.decide("scope_expand", approved=True, by="bob",
                    lease_owner="alice", now=200)
    assert gate.approved("eng-1", "scope_expand") is False, "过期之后居然放行了"
    expired = [d for d in gate.decisions if d.state is ApprovalState.EXPIRED]
    assert expired, "过期没有留痕"


# ── 5：量具能红（先证明它抓得到）────────────────────────────
def test_probe_would_catch_a_gate_that_allows_self_approval() -> None:
    """**先证明量具抓得到**：伪造一个"自批被放行"的账，判据必须看得出差别。

    前四轮（12/14/15/16）我在"量具能不能红"上栽过四次，这条是补那个洞。

    做法：不走 `decide`，直接造一条 `APPROVED` 且 `decided_by == lease_owner`
    的账，验证 `approved()` 会因此返回 True —— 也就是说，
    如果实现把自批写成 APPROVED，`test_self_approval_is_rejected` 会立刻红。
    """
    gate = _gate()
    with pytest.raises(ApprovalRequired):
        gate.require("scope_expand", now=100)
    forged = gate.decisions[-1]
    forged.state = ApprovalState.APPROVED
    forged.decided_by = "alice"          # 与 lease_owner 相同
    assert gate.approved("eng-1", "scope_expand") is True, (
        "伪造的自批没能让 approved() 变 True —— 那说明这条量具对真实现无区分力"
    )
