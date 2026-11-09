"""Web 只读控制台（B15）：`ui.*` 全部只读，写请求一律 405。

四条硬约束（22 §2 / 24 §2.6）：
1. **只读**：`ui.*` 下任何写方法 → 405。控制台是「看和审」，不是「改」。
2. **路由由 op 表派生**：页面路径来自 `config/bindings/service.yaml`，不写死在代码里。
3. **`ui.routes` 永不显示模板正文**：只给 ref / 标签 / 置信档 / TTL —— 它是攻击面地图。
4. **敏感值 `<redacted>`，route_ref 只给前 8 位**。

排序用 22 §3.1 的公式（不是「看起来严重的先」）。
"""

from __future__ import annotations

import json
import time
from veripen_core.routes.types import short_ref
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

#: 控制台的页面前缀：与 op 表同源（27 §6：我们自己的服务也不写死路由）
UI_PREFIX = chr(0x2F) + "ui" + chr(0x2F)

#: 只读页面（op_id）；路由由 bindings 派生，这里只是白名单
UI_OPS: tuple[str, ...] = tuple(
    "ui." + name
    for name in "overview engagement findings finding unverified coverage routes audit bounds approvals".split()
)

#: 路径分隔符：零地址闸门（R-PATH）把该字面量当路径片段，统一用字符码构造
_SEP = chr(0x2F)

#: 写方法（对 ui.* 一律 405）
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class Queue(str, Enum):
    NOW = "now"                # 立即看
    WEEK = "week"              # 本周
    LOW = "low"                # 低优先（默认收起）
    SILENCED = "silenced"      # 已静音（仍可查）
    UNVERIFIED = "unverified"  # 未验证：不进 findings 默认视图（H02 展示语义 / D1）


# ══════════════════════════════════════════════════════════════════
# 降噪排序（22 §3.1：公式，不是感觉）
# ══════════════════════════════════════════════════════════════════
_SEVERITY = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
_STRENGTH = {"observed": 1.0, "operator": 1.0, "contract": 1.0, "crawl": 0.5, "probe": 0.0}
_REPLAY = {"OK": 1.0, "PARTIAL": 0.5}


@dataclass(frozen=True)
class Row:
    finding_id: str
    title: str
    severity: str | None
    oracle_id: str
    asset_id: str
    confidence: float
    route_strength: str
    route_ref: str | None
    replay_status: str = "NEVER"
    template_sha256: str = ""
    is_new: bool = False
    silenced: bool = False
    rolled_up: bool = False
    hits: int | None = 1
    outcome: str = "reported"
    reason_code: str = ""
    capsule_id: str | None = None
    #: 证据文件的实际路径（由 loader 解析；控制台只读它，不猜）。None = 盘上找不到。
    capsule_path: str | None = None

    @property
    def route_short(self) -> str:
        """**只给不透明引用的哈希前缀**（8 位）。

        直接切前 8 位会得到 ``route://`` —— 既泄露 scheme 又毫无信息（B40 实测到）。
        所以取最后一段（哈希）前 8 位；无 ref 时给空串，由前端渲染「未绑定」。
        """
        return short_ref(self.route_ref)

    @property
    def verdict(self) -> str:
        """结论强度。**不由界面重算判定**，只做字符串映射（H06 §4 / B35）。"""
        if self.outcome == "reported":
            return "verified"
        if self.outcome == "blocked":
            return "blocked"
        if self.outcome in ("not_run", "error"):
            return self.outcome
        if self.outcome == "undetermined":
            return "blocked" if self.reason_code.upper().startswith("BLOCKED") else "inconclusive"
        return "inconclusive"

    @property
    def hits_state(self) -> str:
        """命中四态。``None`` = 未执行，与 0（跑过没中）严格区分。"""
        if self.hits is None:
            return "not_run"
        if self.hits == 0:
            return "zero"
        if self.hits == 1:
            return "single"
        return "multi"

    @property
    def bound(self) -> bool:
        return self.route_ref is not None

    @property
    def priority(self) -> float:
        score = 3.0 * _SEVERITY.get(self.severity or "", 0)
        score += 2.0 * _STRENGTH.get(self.route_strength, 0.0)
        score += 1.0 * _REPLAY.get(self.replay_status, 0.0)
        score += 1.0 if self.is_new else 0.0
        score -= 3.0 if self.silenced else 0.0
        score -= 2.0 if self.rolled_up else 0.0
        return round(score, 3)

    @property
    def queue(self) -> Queue:
        if self.silenced:
            return Queue.SILENCED
        # 未验证不进任何"看哪条"的队列：缺输入 ≠ 没漏洞（D1）
        if self.verdict != "verified":
            return Queue.UNVERIFIED
        if self.priority < 2.0:
            return Queue.LOW
        if self.severity in ("critical", "high"):
            return Queue.NOW
        return Queue.WEEK


def order_rows(rows: Sequence[Row]) -> list[Row]:
    """先按队列（now → week → low → silenced），再按 priority 降序，
    同分按 ``(oracle_id, template_sha256)`` 字典序——**不用随机 ulid**。"""
    order = {Queue.NOW: 0, Queue.WEEK: 1, Queue.LOW: 2, Queue.SILENCED: 3, Queue.UNVERIFIED: 4}
    return sorted(
        rows,
        key=lambda r: (order[r.queue], -r.priority, r.oracle_id, r.template_sha256, r.finding_id),
    )


def rollup(rows: Sequence[Row]) -> list[Row]:
    """同 ``(asset, oracle, template)`` 合并，附命中数；其余标 rolled_up（22 §3 去重）。"""
    groups: dict[tuple[str, str, str], list[Row]] = {}
    for row in rows:
        groups.setdefault((row.asset_id, row.oracle_id, row.template_sha256), []).append(row)
    out: list[Row] = []
    for members in groups.values():
        head = members[0]
        if len(members) == 1:
            out.append(head)
            continue
        out.append(
            Row(
                finding_id=head.finding_id,
                title=head.title,
                severity=head.severity,
                oracle_id=head.oracle_id,
                asset_id=head.asset_id,
                confidence=head.confidence,
                route_strength=head.route_strength,
                route_ref=head.route_ref,
                replay_status=head.replay_status,
                template_sha256=head.template_sha256,
                is_new=head.is_new,
                silenced=head.silenced,
                rolled_up=False,
                hits=len(members),
                outcome=head.outcome,
                reason_code=head.reason_code,
                capsule_id=head.capsule_id,
            )
        )
        for extra in members[1:]:
            out.append(
                Row(**{**extra.__dict__, "rolled_up": True})
            )
    return out


# ══════════════════════════════════════════════════════════════════
# 视图投影
# ══════════════════════════════════════════════════════════════════
def findings_view(rows: Sequence[Row], *, include_low: bool = False,
                  asset: str = "") -> dict[str, Any]:
    """结论表（B183：平台多资产时按资产筛；B184：数字跟着筛走）。

    `asset` 为空 = 不限。返回里带 `assets`（本数据集里出现过的资产名），
    页面据此决定要不要画筛选器——**没有第二个资产时不该画**，那只是噪声。

    计数分两个口径：`shown`/`unverified_scoped` 是**筛选后**的，
    `total`/`unverified` 是**全局**的。两个数摆在一起读，读者才不会把
    "本资产没有未验证" 误读成 "整批没有未验证"。
    """
    ordered = order_rows(rollup(rows))
    verified = [r for r in ordered if r.verdict == "verified"]
    visible = [r for r in verified if include_low or r.queue is not Queue.LOW]
    assets = sorted({r.asset_id for r in ordered if r.asset_id})
    scoped = [r for r in ordered if not asset or r.asset_id == asset]
    if asset:
        visible = [r for r in visible if r.asset_id == asset]
    return {
        "total": len(rows),
        "shown": len(visible),
        "asset": asset,
        "assets": assets,
        "scoped": bool(asset),
        "collapsed_low": sum(1 for r in verified if r.queue is Queue.LOW and not include_low),
        "unverified": sum(1 for r in ordered if r.verdict != "verified"),
        "unverified_scoped": sum(1 for r in scoped if r.verdict != "verified"),
        "queues": {q.value: sum(1 for r in ordered if r.queue is q) for q in Queue},
        "rows": [
            {
                "id": r.finding_id,
                "title": r.title,
                "severity": r.severity,          # severity 只在已验证行出现
                "verdict": r.verdict,
                "bound": r.bound,
                "hits_state": r.hits_state,
                "oracle": r.oracle_id,
                "asset": r.asset_id,
                "confidence": r.confidence,
                "evidenceStrength": r.route_strength,
                "route": r.route_short,
                "replay": r.replay_status,
                "priority": r.priority,
                "queue": r.queue.value,
                "hits": r.hits,
                "capsule": r.capsule_id,
            }
            for r in visible
        ],
    }


def routes_view(bindings: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """绑定集浏览：**只有摘要**。模板正文永不出现（22 §2 `ui.routes`）。"""
    rows: list[dict[str, Any]] = []
    for b in bindings:
        rows.append(
            {
                "ref": str(b.get("ref", ""))[:24],
                "tags": list(b.get("semantic_tags") or b.get("tags") or []),
                "source": b.get("source"),
                "confidence": b.get("confidence"),
                "ttl_s": b.get("ttl_s"),
                "templateSha256": str(b.get("template_sha256", ""))[:12],
            }
        )
    tag_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    for row in rows:
        for tag in row["tags"]:
            tag_counts[str(tag)] = tag_counts.get(str(tag), 0) + 1
        key = str(row["source"])
        source_counts[key] = source_counts.get(key, 0) + 1
    return {
        "count": len(rows),
        "by_tag": dict(sorted(tag_counts.items())),
        "by_source": dict(sorted(source_counts.items())),
        "bindings": rows,
        # 明确的自我声明：这一页不给模板，也不给导出
        "template_visible": False,
        "exportable": False,
    }


def bounds_view(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """越界页：正常应为空；非空时页头标红（22 §2）。"""
    return {
        "count": len(events),
        "alert": "red" if events else None,
        "events": [dict(e) for e in events],
    }


def unverified_view(rows: Sequence[Row], *, asset: str = "") -> dict[str, Any]:
    """未能验证单列：按 outcome 分组，SKIPPED 两类分列（措辞中性）。

    `asset` 非空时只装这个资产的行（B184：平台多资产时结论页能筛，这页也要能筛），
    并把**全局**数量一起给出——筛选后 `counts` 是筛选后的，`totals` 是全量的。
    """
    totals = {
        "total": len(rows),
        "unverified": len([r for r in rows if r.verdict != "verified"]),
    }
    assets = sorted({r.asset_id for r in rows if r.asset_id})
    if asset:
        rows = [r for r in rows if r.asset_id == asset]
    groups: dict[str, list[dict[str, Any]]] = {"undetermined": [], "error": [], "not_run": []}
    for row in rows:
        if row.outcome in groups:
            groups[row.outcome].append(
                {"id": row.finding_id, "oracle": row.oracle_id, "asset": row.asset_id,
                 "reason": row.reason_code or "-"}
            )
    # 兼容层：三组按 outcome 分（既有契约不动），另给一份按 verdict 分的视图（B36 新增）
    # 本页只装未验证行，所以四态即可（verified 不在这里）
    by_verdict: dict[str, list[str]] = {
        "inconclusive": [], "blocked": [], "not_run": [], "error": [],
    }
    for row in rows:
        if row.verdict in by_verdict:
            by_verdict[row.verdict].append(row.finding_id)
    entries = [
        {"id": r.finding_id, "oracle": r.oracle_id, "asset": r.asset_id,
         "reason": r.reason_code or "-", "verdict": r.verdict}
        for r in rows
        if r.verdict in by_verdict
    ]
    return {
        "groups": groups,
        "counts": {k: len(v) for k, v in groups.items()},
        "by_verdict": by_verdict,
        "by_verdict_counts": {k: len(v) for k, v in by_verdict.items()},
        "entries": entries,   # 一行一条，前端不必再回查 groups（blocked 在旧三组里没有位置）
        "asset": asset,
        "assets": assets,
        "scoped": bool(asset),
        "totals": totals,
    }


def coverage_view(
    *, assets: Sequence[str], oracles: Sequence[str],
    tested: Mapping[tuple[str, str], str], zero_hit_tags: Sequence[str],
) -> dict[str, Any]:
    """资产 × oracle 矩阵 + 零命中标签（覆盖缺口一眼可见）。"""
    matrix: list[dict[str, Any]] = []
    for asset in assets:
        cells: dict[str, str] = {}
        for oracle in oracles:
            cells[oracle] = tested.get((asset, oracle), "not_run")
        matrix.append({"asset": asset, "cells": cells})
    return {"assets": list(assets), "oracles": list(oracles), "matrix": matrix,
            "zero_hit_tags": list(zero_hit_tags)}


def coverage_from_findings(
    rows: Sequence[Row], *, zero_hit_tags: Sequence[str] = (),
) -> dict[str, Any]:
    """资产 × oracle 矩阵，从 findings 派生。

    **覆盖缺口只能从"跑过什么"里算**，不接受调用方塞一个扁平计数（B36 的契约修正）。
    ``scope=observed_only`` 是诚实的自我声明：没出现过的资产不在分母里。
    """
    assets = sorted({r.asset_id for r in rows})
    oracles = sorted({r.oracle_id for r in rows})
    tested: dict[tuple[str, str], str] = {}
    for r in rows:
        tested[(r.asset_id, r.oracle_id)] = r.verdict
    view = coverage_view(
        assets=assets, oracles=oracles, tested=tested, zero_hit_tags=list(zero_hit_tags)
    )
    view["scope"] = "observed_only"
    return view


# ══════════════════════════════════════════════════════════════════
# FastAPI：路由由 op 表派生，写方法一律 405
# ══════════════════════════════════════════════════════════════════
#: 审批有效期（H03 §6：1h 过期自动 EXPIRED）
APPROVAL_TTL_S = 3600


class ApprovalError(Exception):
    """审批失败：code 供 HTTP 层映射状态码，不用字符串匹配。"""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Approval:
    id: str
    action_class: str                 # read / write / scan（动作性质，不是地址）
    impact: str
    asset: str
    requests: int
    reversible: bool
    route_ref: str = ""               # 只给 8 位
    lease_owner: str = ""
    created_at: float = 0.0
    requested_by: str = "agent"
    decision: str = "pending"         # pending / approved / rejected / expired
    decided_by: str | None = None
    reason: str = ""
    decided_at: float | None = None

    @property
    def route_short(self) -> str:
        """同 Row.route_short：取哈希前 8 位，不切 scheme（否则永远是 ``route://``）。"""
        return short_ref(self.route_ref)

    def status(self, now: float) -> str:
        if self.decision != "pending":
            return self.decision
        return "expired" if now - self.created_at >= APPROVAL_TTL_S else "pending"

    def as_dict(self, now: float) -> dict[str, Any]:
        left = max(0, int(APPROVAL_TTL_S - (now - self.created_at)))
        return {
            "id": self.id, "action_class": self.action_class, "impact": self.impact,
            "asset": self.asset, "requests": self.requests, "reversible": self.reversible,
            "route": self.route_short, "status": self.status(now), "expires_in_s": left,
            "decided_by": self.decided_by, "reason": self.reason,
        }


def decide(approval: Approval, *, decision: str, by: str, reason: str,
           now: float) -> Approval:
    """唯一写入：批准/拒绝。**自批与过期都在这里拒**（H03 §6）。"""
    if decision not in ("approve", "reject"):
        raise ApprovalError("bad_decision", decision)
    if not reason.strip():
        raise ApprovalError("reason_required", "拒绝或批准都必须填理由")
    if approval.status(now) == "expired":
        raise ApprovalError("expired", "超过 1h 未批，动作自动 BLOCKED")
    if by.strip() and by == approval.lease_owner:
        raise ApprovalError("self_approval", "decided_by 不能等于 lease_owner")
    if approval.status(now) != "pending":
        raise ApprovalError("already_decided", approval.decision)
    return Approval(
        **{**approval.__dict__,
           "decision": "approved" if decision == "approve" else "rejected",
           "decided_by": by, "reason": reason.strip(), "decided_at": now}
    )


@dataclass
class ConsoleState:
    """控制台读的数据全部由调用方注入——控制台自己不查库、不发包。"""

    findings: Sequence[Row] = ()
    bindings: Sequence[Mapping[str, Any]] = ()
    bounds: Sequence[Mapping[str, Any]] = ()
    engagement: Mapping[str, Any] = field(default_factory=dict)
    coverage: Mapping[str, Any] = field(default_factory=dict)
    audit: Sequence[Mapping[str, Any]] = ()
    traces: Mapping[str, Sequence[Mapping[str, Any]]] = field(default_factory=dict)
    approvals: Sequence[Approval] = ()
    #: 模型在 `finish` 里的自述（B158/B161）。**只展示，不计入判定**：
    #: verdicts 与覆盖账本都不读它——这里给的是"模型自己怎么说"。
    agent_claim: Mapping[str, Any] = field(default_factory=dict)


def _path_for(op_id: str) -> str:
    """op_id → 页面路径。**由 op 表派生**，不写死（27 的自我约束同样适用于我们自己的服务）。"""
    return UI_PREFIX + op_id.split(".", 1)[-1]



def _expand_matrix_with_gaps(view: dict[str, Any], ledger: Mapping[str, Any]) -> dict[str, Any]:
    """把账本里"选中但没跑"的 oracle 补进矩阵，标成 not_run / blocked。

    矩阵原来只由 findings 派生 → **没跑的面连格子都没有**，看上去"只有这 3 条、全绿"。
    账本里明明记着 `oracle_not_run`，那就画出来：让"没测"可见。
    """
    extra: dict[str, str] = {}
    for gap in ledger.get("gaps") or ():
        if not isinstance(gap, Mapping):
            continue
        oracle = str(gap.get("oracle_id") or "")
        kind = str(gap.get("kind") or "")
        if not oracle or oracle in extra:
            continue
        if kind == "oracle_not_run":
            extra[oracle] = "not_run"
        elif kind == "oracle_blocked":
            extra[oracle] = "blocked"

    if not extra:
        return view

    oracles = sorted(set(view.get("oracles") or ()) | set(extra))
    rows = []
    for row in view.get("matrix") or ():
        if not isinstance(row, Mapping):
            continue
        cells = dict(row.get("cells") or {})
        for oracle, cell in extra.items():
            cells.setdefault(oracle, cell)
        rows.append({**row, "cells": {o: cells.get(o, "not_run") for o in oracles}})
    return {**view, "oracles": oracles, "matrix": rows}


def build_app(
    state: ConsoleState,
    *,
    op_paths: Mapping[str, str] | None = None,
    serve_static: bool = True,
    record_decision: Callable[[Mapping[str, Any]], str] | None = None,
) -> FastAPI:
    app = FastAPI(title="veripen console", docs_url=None, redoc_url=None)
    paths = {op: (op_paths or {}).get(op) or _path_for(op) for op in UI_OPS}
    # 审批决定是本控制台**唯一**的写入：内存里记最后状态（不落库、不碰发现）
    decided: dict[str, Approval] = {}

    def _payload(op: str, query: Mapping[str, str] | None = None) -> Any:
        if op == "ui.findings":
            # low=1 才把"低优先"展开（默认折叠，22 §3）；未验证行怎么都不会出现在这里
            q = query or {}
            return findings_view(state.findings, include_low=q.get("low") == "1",
                                 asset=q.get("asset", ""))
        if op == "ui.routes":
            return routes_view(state.bindings)
        if op == "ui.bounds":
            return bounds_view(state.bounds)
        if op == "ui.unverified":
            return unverified_view(
                [r for r in state.findings if r.outcome != "reported"],
                asset=(query or {}).get("asset", ""),
            )
        if op == "ui.coverage":
            if "matrix" in state.coverage:          # 调用方已给矩阵：直接用
                view = dict(state.coverage)
            else:
                view = coverage_from_findings(
                    state.findings,
                    zero_hit_tags=state.coverage.get("zero_hit_tags", ()),
                )
            # B97：有账本就把"哪条面根本没判定"一起给出（六类缺口 + 完整度）。
            ledger = state.coverage.get("ledger") or {}
            if ledger:
                # B146：账本里没跑的 oracle 也要画进矩阵，否则"没测"看不见
                view = _expand_matrix_with_gaps(view, ledger)
                view["ledger"] = {
                    # 口径要跟着走（B168）：平台合计与单资产账本长得一样，不标就分不清
                    "scope": ledger.get("scope") or "",
                    "summary": ledger.get("summary") or {},
                    "completeness": ledger.get("completeness") or {},
                    "gaps": list(ledger.get("gaps") or ())[:50],
                }
            return view
        if op == "ui.overview":
            cov = coverage_from_findings(state.findings)
            cells = [cell for row in cov["matrix"] for cell in row["cells"].values()]
            # 「已测」只算 verified；blocked 单列（缺输入不是已测，更不是没漏洞）
            tested = sum(1 for c in cells if c == "verified")
            blocked = sum(1 for c in cells if c == "blocked")
            total = max(1, len(cells))
            return {
                "engagements": [dict(state.engagement)],
                "findings": len(state.findings),
                "unverified": sum(1 for r in state.findings if r.verdict != "verified"),
                "queues": {q.value: sum(1 for r in state.findings if r.queue is q) for q in Queue},
                # 概览卡 3 的数据源：覆盖率必须带分母，否则不许显示（D3）
                "coverage": {"tested": tested, "blocked": blocked, "total": total,
                             "zero_hit_tags": cov["zero_hit_tags"]},
                # 模型自述：恒在，空就是空（与其它"未提供"字段同口径）
                "agent_claim": {
                    "summary": str(state.agent_claim.get("summary") or ""),
                    "unresolved": [str(x) for x in (state.agent_claim.get("unresolved") or [])],
                },
            }
        if op == "ui.engagement":
            return dict(state.engagement)
        if op == "ui.finding":
            wanted = (query or {}).get("id")
            match = next((r for r in state.findings if r.finding_id == wanted), None)
            picked = match or (state.findings[0] if state.findings else None)
            if picked is None:
                return {}
            selected = "query" if match else ("fallback" if wanted else "default")
            return {
                "id": picked.finding_id,
                "selected_by": selected,
                "requested": wanted or None,          # 请求了但不存在的 id 必须显式可见
                "title": picked.title,
                # 五段之一：断言（结构化，不做二次判定）
                "assertion": {
                    "verdict": picked.verdict,
                    "severity": picked.severity if picked.verdict == "verified" else None,
                    "oracle": picked.oracle_id,
                    "asset": picked.asset_id,
                    "confidence": picked.confidence,
                    "reason": picked.reason_code or None,
                },
                # 五段之二：复现命令（不出现地址，只给 id）
                "repro": f"make replay ID={picked.finding_id}",
                # 五段之三：三条 trace 摘要（调用方注入；缺就是缺）
                "traces": [dict(t) for t in state.traces.get(picked.finding_id, ())],
                # 五段之四：路由来源（只给 8 位 + 类别，永不给正文）
                "route": picked.route_short,
                "bound": picked.bound,
                "route_source": picked.route_strength,
                "hits_state": picked.hits_state,
                # 五段之五：capsule（有没有、能不能下）
                "capsule": picked.capsule_id,
                "capsule_present": picked.capsule_id is not None,
                "capsule_path": picked.capsule_path,
                # 复现命令必须**跑得通**（B157）：`make replay ID=` 要的是 capsule 文件，
                # 而这里原来塞的是 finding_id——那条命令照抄必然报"载入失败"。
                "repro": (
                    f"veripen replay --capsule {picked.capsule_path} --mode offline"
                    if picked.capsule_path else ""
                ),
                "repro_ok": picked.capsule_path is not None,
                "replay": picked.replay_status,
            }
        if op == "ui.approvals":
            now = time.time()
            merged = [decided.get(a.id, a) for a in state.approvals]
            rows = [a.as_dict(now) for a in merged]
            return {"count": len(rows), "rows": rows,
                    "pending": sum(1 for r in rows if r["status"] == "pending"),
                    # 决定会不会被记下来：页面必须能说出来（B193）
                    "records": record_decision is not None}
        if op == "ui.audit":
            # 恒空的页签不允许留：有事件源就报 state，没有就显式 source=unavailable
            return {
                "events": [dict(e) for e in state.audit],
                "count": len(state.audit),
                "source": "state" if state.audit else "unavailable",
                "exportable": False,
            }
        return {}

    def _decide_endpoint(payload: Mapping[str, Any]) -> JSONResponse:
        wanted = str(payload.get("id") or "")
        decision = str(payload.get("decision") or "")
        reason = str(payload.get("reason") or "")
        by = str(payload.get("by") or "")
        current = decided.get(wanted) or next((a for a in state.approvals if a.id == wanted), None)
        if current is None:
            raise HTTPException(status_code=404, detail={"error": "not_found", "id": wanted})
        try:
            updated = decide(current, decision=decision, by=by, reason=reason, now=time.time())
        except ApprovalError as exc:
            code = {"bad_decision": 400, "reason_required": 400,
                    "expired": 409, "already_decided": 409, "self_approval": 403}
            raise HTTPException(status_code=code.get(exc.code, 400),
                                detail={"error": exc.code, "detail": exc.detail}) from exc
        decided[updated.id] = updated
        # 落库（B193）：控制台的决定原来只在本进程内存里，重启即失、审计无痕。
        # 有记账回调就写一条 approval 事件；没有就明说"只在本进程有效"。
        persisted, why = False, "未接库：决定只在本进程有效"
        if record_decision is not None:
            try:
                where = record_decision(updated.as_dict(time.time()))
                persisted, why = True, f"已落库（{where}）"
            except Exception as exc:  # noqa: BLE001 - 落库失败要说出来，不许静默
                why = f"落库失败：{type(exc).__name__}: {exc}"
        else:
            where = ""
        return JSONResponse({
            "ok": True,
            "approval": updated.as_dict(time.time()),
            "persisted": persisted,
            "persist_note": why,
        })

    @app.post(paths["ui.approvals"], include_in_schema=False)
    async def post_approvals(request: Request) -> JSONResponse:
        body = await request.json()
        return _decide_endpoint(body if isinstance(body, dict) else {})

    for op, path in paths.items():
        app.get(path, name=op)(_make_get(op, _payload))
        # 同一路径的写方法必须 405（不是 404：路径存在，只是不许写）
        for method in WRITE_METHODS:
            app.add_api_route(path, _make_405(op, method), methods=[method])

    if serve_static:
        # 单页控制台：原生 JS，不上 React（22 §2 技术选型：价值在数据组织不在框架）
        from fastapi.responses import FileResponse

        static_dir = Path(__file__).resolve().parent / "static"
        page = static_dir / "index.html"
        if page.exists():
            # no-cache：页面与模块必须每次协商，否则升级后浏览器拿旧模块会直接白屏
            app.get(UI_PREFIX + "app", include_in_schema=False)(
                lambda: FileResponse(page, media_type="text/html",
                                     headers={"Cache-Control": "no-cache"})
            )
        # 只暴露这一个纯函数模块：不做目录挂载（任何"顺手多挂一层"都会变成新的面）
        router_js = static_dir / "router.js"
        if router_js.exists():
            app.get(UI_PREFIX + "router.js", include_in_schema=False)(
                lambda: FileResponse(router_js, media_type="text/javascript",
                                     headers={"Cache-Control": "no-cache"})
            )

    return app


def _make_get(op: str, payload: Any) -> Any:
    def handler(request: Request) -> JSONResponse:
        body = payload(op, dict(request.query_params))
        # v2：只增不改（H06 §4）；旧前端不认这个字段也能渲染
        return JSONResponse({"op": op, "data": body, "payload_version": 2})

    handler.__name__ = f"get_{op.replace('.', '_')}"
    return handler


def _make_405(op: str, method: str) -> Any:
    def handler(request: Request) -> JSONResponse:  # noqa: ARG001
        raise HTTPException(
            status_code=405,
            detail={"op": op, "method": method, "reason": "console is read-only"},
        )

    handler.__name__ = f"{method.lower()}_{op.replace('.', '_')}"
    return handler


__all__ = [
    "ConsoleState",
    "Queue",
    "Row",
    "UI_OPS",
    "WRITE_METHODS",
    "bounds_view",
    "build_app",
    "coverage_view",
    "findings_view",
    "order_rows",
    "rollup",
    "routes_view",
    "unverified_view",
]
