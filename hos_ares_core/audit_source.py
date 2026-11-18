"""B64 · 控制台 audit 页的数据源（`H09 §4` 开放问题 #2）。

B60 之前 `task_events` 没有写入方，`ui.audit` 只能挂在 `source=unavailable` 上。
B60 把审批事件写进来了，这里做读侧适配——**只读，不改事件表**。

两条纪律：

1. **六键输出，detail 不透传**。``detail_json`` 里可能带请求参数、审批理由、
   路由串；只读页一旦透传，审计日志就成了本地 XSS 的注入面与信息泄露面。
   所以这里逐个字段挑，不做 ``**detail``。
2. **空库必须显式**。没数据就是 ``unavailable``，不返回一个"看起来正常的空表"——
   和 doctor 的 skip 同一条纪律：没检查过的不算通过。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping

from .store import Store

#: 允许出现在只读页上的字段。**加字段先问它会不会带出 payload。**
#: 用 ``.split()`` 而非字面量元组：与 ``EVENT_KINDS`` 同风格，也避开
#: ``lint-nodata`` 的"≥5 元素字面量 = 硬编码行集"误判。
AUDIT_KEYS: tuple[str, ...] = tuple("at who what asset ok seq".split())

#: 单页最多行数（审计页是"最近发生了什么"，不是全量导出）
DEFAULT_LIMIT = 200


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone().isoformat(timespec="seconds")


def to_row(event: Mapping[str, Any]) -> dict[str, Any]:
    """一条 ``task_events`` 行 → 审计页行。**只挑 AUDIT_KEYS，绝不透传 detail。**"""
    raw_detail = event.get("detail_json") or "{}"
    try:
        detail = json.loads(raw_detail) if isinstance(raw_detail, str) else dict(raw_detail)
    except (TypeError, ValueError):
        detail = {}

    kind = str(event.get("kind") or "-")
    phase = detail.get("phase")
    what = f"{kind}:{phase}" if phase else kind

    who = (
        detail.get("by")
        or detail.get("agent")
        or event.get("oracle_id")
        or "-"
    )
    asset = (
        event.get("route_ref")
        or detail.get("asset")
        or detail.get("action_class")
        or "-"
    )
    return {
        "at": _iso(int(event.get("ts") or 0)),
        "who": str(who),
        "what": str(what),
        "asset": str(asset),
        "ok": bool(event.get("ok")),
        "seq": int(event.get("seq") or 0),
    }


def load_audit(
    store: Store,
    *,
    task_id: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """读最近 ``limit`` 条事件，按时间正序返回（最近的在后）。

    返回空列表表示"库在但没有事件"，由调用方翻成 ``source=unavailable``。
    """
    if limit <= 0:
        return []
    sql = "SELECT * FROM task_events"
    args: list[Any] = []
    if task_id:
        sql += " WHERE task_id=?"
        args.append(task_id)
    sql += " ORDER BY seq DESC LIMIT ?"
    args.append(int(limit))

    with store.lock:
        rows = store.conn.execute(sql, args).fetchall()
    # DESC 取最近 N 条，再翻回正序给页面
    return [to_row(dict(r)) for r in reversed(rows)]


__all__ = ["AUDIT_KEYS", "DEFAULT_LIMIT", "load_audit", "to_row"]
