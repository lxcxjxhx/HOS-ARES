"""B12.8 · 操作台 SSE（34 §2）：**一个端点，五类事件**。

三条约束写在这里，不写在文档里：

1. 事件里**不含模板正文、不含 host/IP**——只有 ``route_ref``（27 N3）；
2. ``act.*`` 事件必须带 ``sandbox_id``，否则前端不该渲染执行卡
   （**看不见它跑了什么命令 = 安全事故**）；
3. SSE 只推**新增**，不推历史；历史走 ``after=seq`` 游标。

另外一条本层独有的边界：**控制台永远改不了 verdict**。
``CONTROL_OPS`` 里没有一条能改结论，这不是靠 UI 不画按钮，是接口上就没有这个操作。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from .routes.addresses import contains_address


class StreamError(RuntimeError):
    """事件非法：带地址、缺 sandbox_id、或类型不在五类里。**fail-closed**。"""

    code = "VP-8008"


class EventType(str, Enum):
    STEP = "step"
    BUDGET = "budget"
    FACT = "fact"
    LEAD = "lead"
    APPROVAL = "approval"


#: 五类事件的必填字段（34 §2 的 jsonc 样例逐字段落地）。
#: 写成 ``"a b c".split()`` 而不是元组字面量：它是一句**要求**，不是一张数据表。
_FIELDS: Mapping[EventType, str] = {
    EventType.STEP: "seq kind ok",
    EventType.BUDGET: "steps tokens leads",
    EventType.FACT: "id subject predicate value evidence_id",
    EventType.LEAD: "lead_id state source verifiable_by",
    EventType.APPROVAL: "approval_id action_class state",
}
REQUIRED: Mapping[EventType, tuple[str, ...]] = {
    k: tuple(v.split()) for k, v in _FIELDS.items()
}


@dataclass(frozen=True)
class Event:
    t: EventType
    payload: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"t": self.t.value, **dict(self.payload)}

    def to_sse(self) -> str:
        """SSE 帧：``event:`` + ``data:``。同一 ``data`` 行内不换行。"""
        body = json.dumps(self.as_dict(), ensure_ascii=False, sort_keys=True)
        return f"event: {self.t.value}\ndata: {body}\n\n"


def _assert_clean(value: Any, *, path: str) -> None:
    """递归扫字符串：出现地址即拒。**模板正文也在这里被顺手拦住**（它必然含路径分隔）。"""
    if isinstance(value, str):
        if contains_address(value):
            raise StreamError(f"{path} 出现地址：{value[:40]!r}（事件里只许有 route_ref）")
        return
    if isinstance(value, Mapping):
        for k, v in value.items():
            _assert_clean(v, path=f"{path}.{k}")
        return
    if isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _assert_clean(v, path=f"{path}[{i}]")


def make(etype: str | EventType, payload: Mapping[str, Any]) -> Event:
    """造一个合法事件；任一约束不满足即抛。"""
    try:
        t = EventType(etype) if not isinstance(etype, EventType) else etype
    except ValueError as exc:
        raise StreamError(f"未知事件类型 {etype!r}（五类：{[e.value for e in EventType]}）") from exc

    missing = [k for k in REQUIRED[t] if k not in payload]
    if missing:
        raise StreamError(f"{t.value}: 缺字段 {missing}")

    # act.* 事件必须带 sandbox_id：没有它，前端画出来的执行卡是假的
    if t is EventType.STEP and payload.get("kind") == "act" and not payload.get("sandbox_id"):
        raise StreamError(
            "act 事件缺 sandbox_id：看不见它跑了什么命令 = 安全事故（34 §2 约束 2）"
        )

    _assert_clean(payload, path=t.value)
    return Event(t=t, payload=dict(payload))


# ══════════════════════════════════════════════════════════════════
# 只推新增：游标来自 task_events.seq
# ══════════════════════════════════════════════════════════════════
class Cursor:
    """``after=seq`` 游标。**SSE 不推历史**，历史走这个游标拉。"""

    __slots__ = ("after",)

    def __init__(self, after: int = 0) -> None:
        if after < 0:
            raise StreamError("after 不能为负")
        self.after = after

    def advance(self, seq: int) -> None:
        if seq > self.after:
            self.after = seq

    def take(self, rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
        """只保留 ``seq > after`` 的行，并把游标推到最大 seq。"""
        fresh = [r for r in rows if int(r.get("seq") or 0) > self.after]
        for r in fresh:
            self.advance(int(r.get("seq") or 0))
        return fresh


def events_from_rows(rows: Iterable[Mapping[str, Any]], *, sandbox_id: str = "") -> list[Event]:
    """把 ``task_events`` 的行转成 SSE 事件。**行里的地址在这里被拦下**。"""
    out: list[Event] = []
    for r in rows:
        kind = str(r.get("kind") or "plan")
        raw_detail = r.get("detail")
        detail: Mapping[str, Any] = raw_detail if isinstance(raw_detail, dict) else {}
        if kind == "oracle":
            payload = {
                "seq": int(r.get("seq") or 0), "kind": "verify",
                "oracle_id": r.get("oracle_id"), "route_ref": r.get("route_ref"),
                "ok": bool(r.get("ok")), "elapsed_ms": r.get("elapsed_ms"),
                "summary": str(detail.get("summary") or ""),
            }
        else:
            payload = {
                "seq": int(r.get("seq") or 0), "kind": kind if kind != "http" else "act",
                "route_ref": r.get("route_ref"), "ok": bool(r.get("ok")),
                "elapsed_ms": r.get("elapsed_ms"),
                "summary": str(detail.get("summary") or ""),
                "persisted_ref": detail.get("persisted_ref"),
            }
            if payload["kind"] == "act" and sandbox_id:
                payload["sandbox_id"] = sandbox_id
        out.append(make(EventType.STEP, payload))
    return out


# ══════════════════════════════════════════════════════════════════
# 控制边界（34 §3）：能管什么、永不可管什么
# ══════════════════════════════════════════════════════════════════
class ControlOp(str, Enum):
    PAUSE = "task.pause"
    RESUME = "task.resume"
    BUDGET_DELTA = "task.budget_delta"
    APPROVE = "task.approve"
    REJECT = "task.reject"
    INJECT_HINT = "task.inject_hint"
    REDISCOVER = "task.rediscover"


#: 允许的控制操作。**没有 verdict / severity**——接口层就不存在这个动作。
CONTROL_OPS: frozenset[str] = frozenset(op.value for op in ControlOp)

#: 明确不存在的操作：出现在请求里即拒（而不是静默忽略）
FORBIDDEN_OPS: frozenset[str] = frozenset(
    {"task.set_verdict", "task.edit_finding", "task.export_template", "task.copy_address"}
)

#: 注入 selector 提示只接受 18 §6 的标签之一 + 一句理由，**不接受自由文本**
SELECTOR_TAGS: frozenset[str] = frozenset(
    "object_read object_list admin_function normal_function param_injectable "
    "url_fetch file_read redirect_entry echo_param token_issue protected_read "
    "profile_write profile_read rate_sensitive content_write content_read "
    "api_read graphql_endpoint".split()
)

#: 地址形态（用于拒绝 hint 里夹带的东西）
_HINT_MAX = 120


def validate_control(op: str, args: Mapping[str, Any]) -> ControlOp:
    """校验一个控制请求。**越界不是"忽略"，是"拒"。**"""
    if op in FORBIDDEN_OPS:
        raise StreamError(
            f"{op} 不存在：控制台不能改 verdict / 改严重度 / 导出模板 / 复制地址（34 §3）"
        )
    if op not in CONTROL_OPS:
        raise StreamError(f"未知控制操作 {op!r}（允许 {sorted(CONTROL_OPS)}）")

    if op == ControlOp.INJECT_HINT.value:
        tag = str(args.get("tag") or "")
        reason = str(args.get("reason") or "")
        if tag not in SELECTOR_TAGS:
            raise StreamError(f"selector 提示只接受 18 §6 的标签，收到 {tag!r}")
        if not reason.strip():
            raise StreamError("selector 提示必须附一句理由")
        if len(reason) > _HINT_MAX:
            raise StreamError(f"理由 {len(reason)} 字 > {_HINT_MAX}")
        if contains_address(reason) or "route://" in reason:
            raise StreamError("理由里不许带地址或 route_ref：那是「人给模型指路」")
        return ControlOp(op)

    if op == ControlOp.BUDGET_DELTA.value:
        delta = args.get("delta")
        if not isinstance(delta, int) or delta == 0:
            raise StreamError("budget_delta 必须是非零整数")
        if delta > 0 and not args.get("reason"):
            raise StreamError("加预算必须写理由")

    return ControlOp(op)


__all__ = [
    "CONTROL_OPS",
    "FORBIDDEN_OPS",
    "SELECTOR_TAGS",
    "ControlOp",
    "Cursor",
    "Event",
    "EventType",
    "REQUIRED",
    "StreamError",
    "events_from_rows",
    "make",
    "validate_control",
]
