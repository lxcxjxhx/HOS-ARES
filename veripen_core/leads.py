"""B12.7 · 线索闭环与强制验证（35 §2/§3/§7）。

这篇规格存在的理由只有一句：
**传统扫描器打了 500 个探针、报了 3 个洞，另外 497 个去哪了没人知道。**
线索账本让"打过的每一下"都有归宿。

四条机制：

1. ``verifiable_by`` 为空的线索产生即 ``expired``，**不占预算**（垃圾线索不该拖垮收敛）；
2. 有 pending lead 时，下一动作**必须**从它的 ``verifiable_by`` 里选（强制闭环）；
3. ``pending_leads == 0`` 才允许 DONE；耗尽则标 ``budget_exhausted=leads``；
4. 闭环率 ``(verified+refuted)/total ≥ 0.60``——太低说明瞎打，太高要怀疑只挑软柿子。

**Lead 只增不改**：状态变化写新记录 + ``supersedes``（与 03 I10 同源）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence

from .models import new_ulid
from .store import Store, now

#: 闭环率门槛（35 §7）：低了是瞎打，高了要怀疑只挑软柿子
MIN_CLOSURE_RATE = 0.60


class LeadState(str, Enum):
    PENDING = "pending"
    VERIFYING = "verifying"
    VERIFIED = "verified"
    REFUTED = "refuted"
    UNVERIFIABLE = "unverifiable"
    EXPIRED = "expired"

    @property
    def closes(self) -> bool:
        """已证 / 已否才算闭环。unverifiable 是"没能验"，不是闭环。"""
        return self in (LeadState.VERIFIED, LeadState.REFUTED)

    @property
    def occupies_budget(self) -> bool:
        """只有 pending / verifying 占预算；expired 与 unverifiable 不占。"""
        return self in (LeadState.PENDING, LeadState.VERIFYING)


class LeadError(RuntimeError):
    """线索记账违规：死线索占用预算、状态非法迁移、闭环被跳过。"""

    code = "VP-8007"


@dataclass(frozen=True)
class Lead:
    lead_id: str
    task_id: str
    asset_id: str
    source_action: str
    raw_ref: str
    summary: str
    verifiable_by: tuple[str, ...]
    selector_hint: str | None = None
    confidence_hint: float = 0.0
    state: LeadState = LeadState.PENDING
    supersedes: str | None = None
    created_at: int = 0

    @property
    def is_dead(self) -> bool:
        return not self.verifiable_by


class LeadLedger:
    """``leads`` 表的读写封装。**没有 update**：状态变化走 ``supersede``。"""

    def __init__(self, store: Store) -> None:
        self.store = store

    # ── 写入 ────────────────────────────────────────────────────
    def record(
        self, *, task_id: str, asset_id: str, source_action: str, raw_ref: str,
        summary: str, verifiable_by: Sequence[str], selector_hint: str | None = None,
        confidence_hint: float = 0.0,
    ) -> Lead:
        """记一条线索。``verifiable_by`` 为空 → 直接 ``expired``，不占预算。"""
        if not summary.strip():
            raise LeadError("线索摘要不能为空：说不清是什么的线索等于没打")
        if len(summary) > 200:
            raise LeadError(f"线索摘要 {len(summary)} 字 > 200（35 §2）")
        if not 0.0 <= confidence_hint <= 1.0:
            raise LeadError(f"confidence_hint={confidence_hint} 越界（仅用于排序，0–1）")

        vb = tuple(dict.fromkeys(str(x) for x in verifiable_by if str(x).strip()))
        state = LeadState.PENDING if vb else LeadState.EXPIRED
        lead = Lead(
            lead_id=new_ulid(), task_id=task_id, asset_id=asset_id,
            source_action=source_action, raw_ref=raw_ref, summary=summary,
            verifiable_by=vb, selector_hint=selector_hint,
            confidence_hint=confidence_hint, state=state, created_at=now(),
        )
        self._insert(lead)
        return lead

    def supersede(self, old_id: str, *, state: LeadState, supersedes_note: str = "") -> Lead:
        """状态迁移：写**新记录**并 ``supersedes`` 旧 id。旧的永远不删（I10）。"""
        old = self.get(old_id)
        if old is None:
            raise LeadError(f"线索 {old_id} 不存在")
        if old["state"] == state.value:
            raise LeadError(f"线索 {old_id} 已是 {state.value}：迁移必须产生变化")
        new = Lead(
            lead_id=new_ulid(),
            task_id=str(old["task_id"]),
            asset_id=str(old["asset_id"]),
            source_action=str(old["source_action"]),
            raw_ref=str(old["raw_ref"]),
            summary=str(old["summary"]) + (f"（{supersedes_note}）" if supersedes_note else ""),
            verifiable_by=tuple(json.loads(old["verifiable_by"] or "[]")),
            selector_hint=old["selector_hint"],
            confidence_hint=float(old["confidence_hint"]),
            state=state,
            supersedes=old_id,
            created_at=now(),
        )
        self._insert(new)
        return new

    def _insert(self, lead: Lead) -> None:
        self.store.conn.execute(
            "INSERT INTO leads (id, task_id, asset_id, source_action, raw_ref, summary,"
            " verifiable_by, selector_hint, confidence_hint, state, supersedes, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                lead.lead_id, lead.task_id, lead.asset_id, lead.source_action, lead.raw_ref,
                lead.summary, json.dumps(list(lead.verifiable_by)), lead.selector_hint,
                lead.confidence_hint, lead.state.value, lead.supersedes, lead.created_at,
            ),
        )
        self.store.conn.commit()

    # ── 读取 ────────────────────────────────────────────────────
    def get(self, lead_id: str) -> dict[str, Any] | None:
        row = self.store.conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        return dict(row) if row is not None else None

    def current(self, task_id: str) -> list[dict[str, Any]]:
        """每条线索的**最新状态**：出现在别人 ``supersedes`` 里的旧记录不算数。"""
        rows = self.store.conn.execute(
            "SELECT * FROM leads WHERE task_id=? ORDER BY created_at, id", (task_id,)
        ).fetchall()
        superseded = {str(r["supersedes"]) for r in rows if r["supersedes"]}
        return [dict(r) for r in rows if str(r["id"]) not in superseded]

    def pending(self, task_id: str) -> list[dict[str, Any]]:
        return [r for r in self.current(task_id) if r["state"] == LeadState.PENDING.value]

    def counts(self, task_id: str) -> dict[str, int]:
        out = {s.value: 0 for s in LeadState}
        for r in self.current(task_id):
            out[str(r["state"])] = out.get(str(r["state"]), 0) + 1
        return out

    def closure_rate(self, task_id: str) -> float:
        """闭环率（35 §7）：``(verified+refuted) / total``。"""
        c = self.counts(task_id)
        total = sum(c.values())
        if total == 0:
            return 1.0  # 没打过就谈不上"没闭环"
        return round((c["verified"] + c["refuted"]) / total, 3)


# ══════════════════════════════════════════════════════════════════
# 强制闭环（35 §3）
# ══════════════════════════════════════════════════════════════════
def forced_next_action(pending: Sequence[dict[str, Any]]) -> tuple[str, str] | None:
    """有 pending lead 时，下一动作**必须**来自它的 ``verifiable_by``。

    返回 ``(oracle_id, lead_id)``；没有 pending 返回 ``None``。
    **按 confidence_hint 降序取第一条**——排序只用 hint，hint 不进 finding。
    """
    live = [p for p in pending if p["state"] == LeadState.PENDING.value]
    if not live:
        return None
    best = sorted(live, key=lambda p: (-float(p["confidence_hint"]), str(p["id"])))[0]
    oracles = json.loads(best["verifiable_by"] or "[]")
    if not oracles:
        return None
    return str(oracles[0]), str(best["id"])


def can_finish(pending_count: int) -> tuple[bool, str]:
    """收敛条件③：``pending_leads == 0`` 才允许 DONE。"""
    if pending_count > 0:
        return False, f"pending_leads={pending_count}：先证明或否掉，再收工"
    return True, ""


@dataclass(frozen=True)
class ClosureVerdict:
    """一次闭环结算的结果。``budget_exhausted`` 只在还有 pending 时为真。"""

    done: bool
    reason: str
    pending: int
    rate: float
    budget_exhausted: str | None = None

    @property
    def may_report_done(self) -> bool:
        return self.done and self.budget_exhausted is None


def settle(ledger: LeadLedger, task_id: str) -> ClosureVerdict:
    """任务收尾结算：能不能 DONE，还是要标 ``budget_exhausted=leads``。"""
    pending = len(ledger.pending(task_id))
    rate = ledger.closure_rate(task_id)
    ok, why = can_finish(pending)
    if ok:
        return ClosureVerdict(done=True, reason="pending_leads=0", pending=0, rate=rate)
    return ClosureVerdict(
        done=False, reason=why, pending=pending, rate=rate, budget_exhausted="leads"
    )


# ══════════════════════════════════════════════════════════════════
# 报告「线索」章节（35 §8 第 8 条）：与 finding 分开列
# ══════════════════════════════════════════════════════════════════
def render_leads_section(ledger: LeadLedger, task_id: str) -> str:
    """线索章节。**单独成章**，不许混进 finding 列表——两者证据强度不同。"""
    c = ledger.counts(task_id)
    total = sum(c.values())
    rate = ledger.closure_rate(task_id)
    lines = [
        "## 线索（与 finding 分列）",
        "",
        f"共 {total} 条；闭环率 {rate:.2f}"
        f"（门槛 ≥ {MIN_CLOSURE_RATE:.2f} {'✅' if rate >= MIN_CLOSURE_RATE else '❌'}）",
        "",
        "| 状态 | 数量 | 含义 |",
        "|---|---|---|",
        f"| verified | {c['verified']} | 已证明（另见 §2 已验证发现） |",
        f"| refuted | {c['refuted']} | 已否掉（打过但排除） |",
        f"| unverifiable | {c['unverifiable']} | 没能验证（缺面 / 预算 / 审批，原因见下） |",
        f"| pending | {c['pending']} | 尚未验证（**不许藏**） |",
        f"| verifying | {c['verifying']} | 正在验证 |",
        f"| expired | {c['expired']} | 产生即作废（无可验证 oracle） |",
    ]
    unverified = [
        r for r in ledger.current(task_id)
        if r["state"] in (LeadState.UNVERIFIABLE.value, LeadState.PENDING.value)
    ]
    if unverified:
        lines += ["", "### 未验证明细（分原因）", ""]
        for r in unverified:
            # 原因写在 supersede 时补进摘要的括号里；明细里带全摘要，读的人才知道"为什么没验成"
            lines.append(
                f"- `{str(r['id'])[:12]}` · {r['state']} · {r['source_action']} · "
                f"{r['summary']} · 可验 by "
                f"{', '.join(json.loads(r['verifiable_by'] or '[]')) or '（无）'}"
            )
    return "\n".join(lines) + "\n"


__all__ = [
    "MIN_CLOSURE_RATE",
    "ClosureVerdict",
    "Lead",
    "LeadError",
    "LeadLedger",
    "LeadState",
    "can_finish",
    "forced_next_action",
    "render_leads_section",
    "settle",
]
