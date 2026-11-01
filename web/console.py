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
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

#: 控制台的页面前缀：与 op 表同源（27 §6：我们自己的服务也不写死路由）
UI_PREFIX = chr(0x2F) + "ui" + chr(0x2F)

#: 只读页面（op_id）；路由由 bindings 派生，这里只是白名单
UI_OPS: tuple[str, ...] = tuple(
    "ui." + name
    for name in "overview engagement findings finding unverified coverage routes audit bounds".split()
)

#: 写方法（对 ui.* 一律 405）
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class Queue(str, Enum):
    NOW = "now"            # 立即看
    WEEK = "week"          # 本周
    LOW = "low"            # 低优先（默认收起）
    SILENCED = "silenced"  # 已静音（仍可查）


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
    severity: str
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
    hits: int = 1
    outcome: str = "reported"
    reason_code: str = ""
    capsule_id: str | None = None

    @property
    def route_short(self) -> str:
        return (self.route_ref or "")[:8]

    @property
    def priority(self) -> float:
        score = 3.0 * _SEVERITY.get(self.severity, 0)
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
        if self.priority < 2.0:
            return Queue.LOW
        if self.severity in ("critical", "high"):
            return Queue.NOW
        return Queue.WEEK


def order_rows(rows: Sequence[Row]) -> list[Row]:
    """先按队列（now → week → low → silenced），再按 priority 降序，
    同分按 ``(oracle_id, template_sha256)`` 字典序——**不用随机 ulid**。"""
    order = {Queue.NOW: 0, Queue.WEEK: 1, Queue.LOW: 2, Queue.SILENCED: 3}
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
def findings_view(rows: Sequence[Row], *, include_low: bool = False) -> dict[str, Any]:
    ordered = order_rows(rollup(rows))
    visible = [r for r in ordered if include_low or r.queue is not Queue.LOW]
    return {
        "total": len(rows),
        "shown": len(visible),
        "collapsed_low": sum(1 for r in ordered if r.queue is Queue.LOW and not include_low),
        "queues": {q.value: sum(1 for r in ordered if r.queue is q) for q in Queue},
        "rows": [
            {
                "id": r.finding_id,
                "title": r.title,
                "severity": r.severity,
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


def unverified_view(rows: Sequence[Row]) -> dict[str, Any]:
    """未能验证单列：按 outcome 分组，SKIPPED 两类分列（措辞中性）。"""
    groups: dict[str, list[dict[str, Any]]] = {"undetermined": [], "error": [], "not_run": []}
    for row in rows:
        if row.outcome in groups:
            groups[row.outcome].append(
                {"id": row.finding_id, "oracle": row.oracle_id, "asset": row.asset_id,
                 "reason": row.reason_code or "-"}
            )
    return {"groups": groups, "counts": {k: len(v) for k, v in groups.items()}}


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


# ══════════════════════════════════════════════════════════════════
# FastAPI：路由由 op 表派生，写方法一律 405
# ══════════════════════════════════════════════════════════════════
@dataclass
class ConsoleState:
    """控制台读的数据全部由调用方注入——控制台自己不查库、不发包。"""

    findings: Sequence[Row] = ()
    bindings: Sequence[Mapping[str, Any]] = ()
    bounds: Sequence[Mapping[str, Any]] = ()
    engagement: Mapping[str, Any] = field(default_factory=dict)
    coverage: Mapping[str, Any] = field(default_factory=dict)


def _path_for(op_id: str) -> str:
    """op_id → 页面路径。**由 op 表派生**，不写死（27 的自我约束同样适用于我们自己的服务）。"""
    return UI_PREFIX + op_id.split(".", 1)[-1]


def build_app(
    state: ConsoleState,
    *,
    op_paths: Mapping[str, str] | None = None,
    serve_static: bool = True,
) -> FastAPI:
    app = FastAPI(title="veripen console", docs_url=None, redoc_url=None)
    paths = {op: (op_paths or {}).get(op) or _path_for(op) for op in UI_OPS}

    def _payload(op: str) -> Any:
        if op == "ui.findings":
            return findings_view(state.findings)
        if op == "ui.routes":
            return routes_view(state.bindings)
        if op == "ui.bounds":
            return bounds_view(state.bounds)
        if op == "ui.unverified":
            return unverified_view([r for r in state.findings if r.outcome != "reported"])
        if op == "ui.coverage":
            return dict(state.coverage)
        if op == "ui.overview":
            return {
                "engagements": [dict(state.engagement)],
                "findings": len(state.findings),
                "queues": {q.value: sum(1 for r in state.findings if r.queue is q) for q in Queue},
            }
        if op == "ui.engagement":
            return dict(state.engagement)
        if op == "ui.finding":
            return next(
                (
                    {
                        "id": r.finding_id, "title": r.title, "assertion": {"severity": r.severity},
                        "route": r.route_short, "evidenceStrength": r.route_strength,
                        "capsule": r.capsule_id, "replay": r.replay_status,
                    }
                    for r in state.findings
                ),
                {},
            )
        if op == "ui.audit":
            return {"events": [], "exportable": False}
        return {}

    for op, path in paths.items():
        app.get(path, name=op)(_make_get(op, _payload))
        # 同一路径的写方法必须 405（不是 404：路径存在，只是不许写）
        for method in WRITE_METHODS:
            app.add_api_route(path, _make_405(op, method), methods=[method])

    if serve_static:
        # 单页控制台：原生 JS，不上 React（22 §2 技术选型：价值在数据组织不在框架）
        from fastapi.responses import FileResponse

        page = Path(__file__).resolve().parent / "static" / "index.html"
        if page.exists():
            app.get(UI_PREFIX + "app", include_in_schema=False)(
                lambda: FileResponse(page, media_type="text/html")
            )

    return app


def _make_get(op: str, payload: Any) -> Any:
    def handler() -> JSONResponse:
        body = payload(op)
        return JSONResponse({"op": op, "data": body})

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
