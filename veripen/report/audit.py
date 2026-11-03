"""B53 报告「智能体行为审计」节：把 `task_events` 摊成五张表。

五张表（`H03 §5`）：动作 / 预算 / 拒绝 / 冲突 / 异常。

三条纪律：

1. **只陈述事实，不评价**：不写"做得很好/很差"，那是人工复核的事；
2. **缺失即显式**：某类事件为空要写「（无）」，不许整张表消失（少一张表 = 读者以为没发生）；
3. **确定性**：同一份输入两次渲染逐行相同；表格按 `(seq, kind)` 排序。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

NL = chr(10)

#: 五张表的固定顺序与标题（少一个都算缺件）
SECTIONS = (
    ("actions", "动作账"),
    ("budget", "预算账"),
    ("denied", "拒绝账"),
    ("conflicts", "冲突账"),
    ("errors", "异常账"),
)

#: 五预算的键（`04 §7`）
BUDGET_KEYS = ("steps", "requests", "tokens", "elapsed_s", "tool_calls")


@dataclass(frozen=True)
class AuditInput:
    task_id: str
    events: Sequence[Mapping[str, Any]] = ()
    budget: Mapping[str, Any] = field(default_factory=dict)
    conflicts: Sequence[Mapping[str, Any]] = ()
    limits: Mapping[str, Any] = field(default_factory=dict)


def _buckets(events: Sequence[Mapping[str, Any]]) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    """拒绝账与异常账**互斥**：审批被拒不算"出错"，它是一条正常发生的拒绝。"""
    denied = [e for e in events if e.get("kind") == "approval" or e.get("err_class") == "approval"]
    errored = [
        e for e in events
        if not (e.get("kind") == "approval" or e.get("err_class") == "approval")
        and ((not e.get("ok")) or e.get("err_class") not in (None, ""))
    ]
    return denied, errored


def _sorted_events(events: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return sorted(events, key=lambda e: (int(e.get("seq", 0)), str(e.get("kind", ""))))


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|") if text else "-"


def _table(header: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    lines = ["| " + " | ".join(header) + " |",
             "|" + "|".join("---" for _ in header) + "|"]
    if not rows:
        lines.append("| " + " | ".join(["（无）"] + [""] * (len(header) - 1)) + " |")
        return NL.join(lines)
    for row in rows:
        lines.append("| " + " | ".join(_cell(c) for c in row) + " |")
    return NL.join(lines)


def render_audit_section(audit: AuditInput) -> str:
    """渲染一整节。五张表**始终都在**，空的写「（无）」。"""
    events = _sorted_events(audit.events)

    action_rows = [
        [e.get("seq"), e.get("kind"), e.get("tool") or e.get("oracle_id"), e.get("route_ref"),
         "ok" if e.get("ok") else "fail", e.get("elapsed_ms")]
        for e in events
    ]
    denied, errored = _buckets(events)
    denied_rows = [[e.get("seq"), e.get("tool") or e.get("oracle_id"),
                    e.get("err_class") or "approval", e.get("detail", {})] for e in denied]
    error_rows = [[e.get("seq"), e.get("kind"), e.get("err_class"), e.get("detail", {})] for e in errored]

    budget_rows = []
    for key in BUDGET_KEYS:
        used = audit.budget.get(key)
        limit = audit.limits.get(key)
        budget_rows.append([key, used if used is not None else "未提供",
                            limit if limit is not None else "未提供"])

    conflict_rows = [
        [c.get("subject"), c.get("predicate"),
         (c.get("winner") or {}).get("value"), (c.get("winner") or {}).get("origin"),
         "；".join(str((l or {}).get("value", "")) for l in (c.get("losers") or [])),
         c.get("reason")]
        for c in audit.conflicts
    ]

    blocks = [
        f"## 智能体行为审计（{audit.task_id}）",
        "",
        "> 本节只陈述发生过的动作与用量，不评价好坏；判读属人工复核。",
        "",
        "### 动作账",
        _table(("seq", "kind", "目标", "route_ref", "结果", "耗时ms"), action_rows),
        "",
        "### 预算账",
        _table(("预算", "已用", "上限"), budget_rows),
        "",
        "### 拒绝账",
        _table(("seq", "动作", "原因", "详情"), denied_rows),
        "",
        "### 冲突账",
        _table(("主体", "谓词", "采信值", "来源", "被压住", "依据"), conflict_rows),
        "",
        "### 异常账",
        _table(("seq", "kind", "错误类", "详情"), error_rows),
        "",
    ]
    return NL.join(blocks)


def section_coverage(audit: AuditInput) -> dict[str, int]:
    """给自检用：五张表各自的条目数（空表记 0，**不省略键**）。"""
    events = _sorted_events(audit.events)
    denied, errored = _buckets(events)
    return {
        "actions": len(events),
        "budget": len(BUDGET_KEYS),
        "denied": len(denied),
        "conflicts": len(audit.conflicts),
        "errors": len(errored),
    }


__all__ = ["AuditInput", "BUDGET_KEYS", "SECTIONS", "render_audit_section", "section_coverage"]
