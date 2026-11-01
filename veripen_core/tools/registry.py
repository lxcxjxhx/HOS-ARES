"""工具层（08 §2）：**窄 schema + 分段校验 + 结构化错误回喂 + 输出归一化**。

四条硬规则，全部写在代码里：

1. **参数分两段校验**：外层只认我们拥有的字段且 ``additionalProperties: false``；
   ``params`` 整体交给 oracle 的 ``params_schema``。外层**不解析内层**——
   否则模型把 ``skip_guard: true`` 塞进 ``params``，遇上宽容 schema 会静默通过。
2. **没有地址**：``list_routes`` 只返回 ``route_ref`` 与摘要；没有 ``probe_asset``。
3. **截断不是终点**：头尾切片（尾部优先保护错误码/摘要）+ 全文落盘 + ``persisted_ref``。
4. **一步一调用**：``tool_calls_per_step=1``、``tool_calls_total=200``，超了是 schema 错误，不是软约定。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field

TOOL_CALLS_PER_STEP = 1
TOOL_CALLS_TOTAL = 200
UNKNOWN_TOOL_LIMIT = 3  # 连续 schema 错误上限（08 §2 第 2 条）

#: 外层字段（我们拥有的）—— 只有这些能被透传进来
OUTER_FIELDS: dict[str, tuple[str, ...]] = {
    "list_assets": (),
    "list_routes": ("selector",),
    "run_oracle": ("oracle_id", "route_ref", "params"),
    "rediscover_routes": ("reason",),
    "record_note": ("text",),
    "request_approval": ("action_class", "reason"),
    "finish": ("summary", "unresolved"),
}
#: 已被删除的工具：出现过即拒（08 §2.1 的理由必须留在代码里）
RETIRED_TOOLS = frozenset({"probe_asset"})


class ToolError(RuntimeError):
    code = "VP-4000"


# ══════════════════════════════════════════════════════════════════
# 外层 schema（严格，多余字段直接拒）
# ══════════════════════════════════════════════════════════════════
class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ListRoutesCall(_Strict):
    selector: str | None = None


class RunOracleCall(_Strict):
    oracle_id: str
    route_ref: str
    params: dict[str, Any] = Field(default_factory=dict)


class RediscoverCall(_Strict):
    reason: str


class RecordNoteCall(_Strict):
    text: str


class RequestApprovalCall(_Strict):
    action_class: str
    reason: str = ""


class FinishCall(_Strict):
    summary: str = ""
    unresolved: list[str] = Field(default_factory=list)


SCHEMAS: dict[str, type[_Strict]] = {
    "list_routes": ListRoutesCall,
    "run_oracle": RunOracleCall,
    "rediscover_routes": RediscoverCall,
    "record_note": RecordNoteCall,
    "request_approval": RequestApprovalCall,
    "finish": FinishCall,
}


# ══════════════════════════════════════════════════════════════════
# 输出归一化（08 §2 第 3 条：头尾切片 + 全文落盘）
# ══════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class NormalizedOutput:
    text: str
    truncated: bool
    persisted_ref: str | None
    persisted_size: int | None
    dropped_lines: int = 0
    dropped_bytes: int = 0
    line_truncated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "truncated": self.truncated,
            "persisted_ref": self.persisted_ref,
            "persisted_size": self.persisted_size,
            "dropped_lines": self.dropped_lines,
            "dropped_bytes": self.dropped_bytes,
            "line_truncated": self.line_truncated,
        }


NOTICE_TEMPLATE = "\n…[truncated: {dropped} more bytes in {ref}]…\n"


def bound_and_store(
    text: str,
    *,
    limit: int = 4096,
    store_dir: str | Path | None = None,
) -> NormalizedOutput:
    """按 08 §2 的**六步顺序**切片。顺序不许改：

    1 预留 notice → 2 头部整行 → 3 **尾部整行（错误码/摘要在这）** → 4 超长行硬切
    → 5 反算 dropped → 6 全文落盘。
    """
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return NormalizedOutput(text=text, truncated=False, persisted_ref=None, persisted_size=None)

    ref = _persist(raw, store_dir)
    notice = NOTICE_TEMPLATE.format(dropped=len(raw), ref=ref or "<unpersisted>").encode()
    budget = max(limit - len(notice), 1)
    head_budget = budget * 2 // 3
    tail_budget = budget - head_budget

    head_lines = _head_lines(raw, head_budget)
    tail_lines = _tail_lines(raw, tail_budget)
    kept = b"".join(head_lines) + notice + b"".join(tail_lines)

    line_truncated = False
    if len(kept) > limit:  # 4 单行超预算：硬切并标注
        kept = kept[:limit]
        line_truncated = True

    total_lines = raw.count(b"\n") + 1
    kept_lines = kept.count(b"\n") + 1
    return NormalizedOutput(
        text=kept.decode("utf-8", errors="replace"),
        truncated=True,
        persisted_ref=ref,
        persisted_size=len(raw),
        dropped_lines=max(total_lines - kept_lines, 0),
        dropped_bytes=max(len(raw) - len(kept), 0),
        line_truncated=line_truncated,
    )


def _head_lines(raw: bytes, budget: int) -> list[bytes]:
    out: list[bytes] = []
    used = 0
    for line in raw.splitlines(keepends=True):
        if used + len(line) > budget:
            break
        out.append(line)
        used += len(line)
    return out


def _tail_lines(raw: bytes, budget: int) -> list[bytes]:
    out: list[bytes] = []
    used = 0
    for line in reversed(raw.splitlines(keepends=True)):
        if used + len(line) > budget:
            break
        out.append(line)
        used += len(line)
    return list(reversed(out))


def _persist(raw: bytes, store_dir: str | Path | None) -> str | None:
    if store_dir is None:
        return None
    digest = hashlib.sha256(raw).hexdigest()
    root = Path(store_dir)
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{digest}.txt").write_bytes(raw)
    return f"out/tool-results/{digest[:12]}.txt"


# ══════════════════════════════════════════════════════════════════
# 调用与分派
# ══════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class ToolCall:
    name: str
    args: Mapping[str, Any]


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    data: Mapping[str, Any] = field(default_factory=dict)
    error_class: str | None = None  # schema | route | budget | approval | oracle | unknown_tool
    hint: str = ""
    output: NormalizedOutput | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"ok": self.ok}
        if self.error_class:
            payload["error_class"] = self.error_class
            payload["hint"] = self.hint
        payload.update(self.data)
        if self.output is not None:
            payload["output"] = self.output.as_dict()
        return payload


class Runtime(Protocol):
    """工具执行所依赖的世界。**由调用方注入**，工具层自己不构造。"""

    def list_routes(self, selector: str | None) -> list[dict[str, Any]]: ...

    def run_oracle(self, oracle_id: str, route_ref: str, params: Mapping[str, Any]) -> dict[str, Any]: ...

    def rediscover(self, reason: str) -> dict[str, Any]: ...

    def request_approval(self, action_class: str, reason: str) -> dict[str, Any]: ...


def validate_call(call: ToolCall) -> ToolResult | _Strict:
    """分段校验的第一段：**外层**。多余字段直接拒，不透传。"""
    if call.name in RETIRED_TOOLS:
        return ToolResult(
            ok=False,
            error_class="unknown_tool",
            hint=f"{call.name} 已删除（08 §2.1）：会拼地址的入口不许存在，请用 list_routes/rediscover_routes",
        )
    schema = SCHEMAS.get(call.name)
    if schema is None:
        return ToolResult(ok=False, error_class="unknown_tool", hint=f"未知工具 {call.name}")
    extra = set(call.args) - set(OUTER_FIELDS[call.name])
    if extra:
        return ToolResult(
            ok=False,
            error_class="schema",
            hint=f"{call.name} 不允许的字段：{sorted(extra)}",
        )
    try:
        return schema(**dict(call.args))
    except Exception as exc:  # pydantic 的校验错误统一回喂，不抛
        return ToolResult(ok=False, error_class="schema", hint=str(exc).splitlines()[0][:200])


def dispatch(
    call: ToolCall,
    runtime: Runtime,
    *,
    budget_tokens_left: int | None = None,
    store_dir: str | Path | None = None,
) -> ToolResult:
    """第二段校验 + 执行 + 输出归一化。**工具层不做判定、不猜语义。**"""
    checked = validate_call(call)
    if isinstance(checked, ToolResult):
        return checked

    if isinstance(checked, ListRoutesCall):
        rows = runtime.list_routes(checked.selector)
        return ToolResult(ok=True, data={"routes": rows})

    if isinstance(checked, RunOracleCall):
        out = runtime.run_oracle(checked.oracle_id, checked.route_ref, checked.params)
        return ToolResult(ok=True, data=out)

    if isinstance(checked, RediscoverCall):
        out = runtime.rediscover(checked.reason)
        return ToolResult(ok=True, data=out)

    if isinstance(checked, RecordNoteCall):
        text = checked.text[:500]
        return ToolResult(ok=True, data={"ok": True, "accepted_len": len(text)})

    if isinstance(checked, RequestApprovalCall):
        out = runtime.request_approval(checked.action_class, checked.reason)
        return ToolResult(ok=True, data=out)

    if isinstance(checked, FinishCall):
        return ToolResult(
            ok=True, data={"ok": True, "summary": checked.summary[:500], "unresolved": checked.unresolved}
        )

    return ToolResult(ok=False, error_class="unknown_tool", hint="未实现的分派")


def tool_schemas() -> dict[str, dict[str, Any]]:
    """给模型看的窄 schema（不含任何地址相关字段）。"""
    return {name: {"required": list(model.model_fields), "additionalProperties": False}
            for name, model in SCHEMAS.items()}


__all__ = [
    "OUTER_FIELDS",
    "RETIRED_TOOLS",
    "TOOL_CALLS_PER_STEP",
    "TOOL_CALLS_TOTAL",
    "NormalizedOutput",
    "Runtime",
    "ToolCall",
    "ToolError",
    "ToolResult",
    "bound_and_store",
    "dispatch",
    "tool_schemas",
    "validate_call",
]
