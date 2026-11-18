"""能力边界（B396/B397）：**这一轮不可能给出结论**的判定，及其渲染。

三处共用一份实现——`run` / `hunt` / `platform` 的报告都要有这段，否则同一个产品
在三条路径上对"能测什么"的表述不一致（企业交付最忌讳这种）。

四类来源：
- `needs_sequence`：判据要多步（harness 一次只发一个请求）
- `unmeasurable`：判定函数在，但客户端侧观测不到
- `unreachable`：选择器不在契约字表（操作员根本写不进去）
- `contract_dropped`：选择器被契约解析器主动丢弃
"""

from __future__ import annotations

from typing import Mapping, Sequence

#: 类别 → 报告里的中文标签
_LABELS = {
    "needs_sequence": "需要多步序列",
    "unmeasurable": "从客户端侧测不出",
    "discoverable": "契约写不进，需 --discover",
    "unreachable": "两条路径都选不中",
    "contract_dropped": "标签被契约解析器丢弃",
}


def capability_limits() -> dict[str, list[tuple[str, str]]]:
    """当前实现的能力边界（四类，各带原因）。空桶不出现。"""
    from hos_ares_contract.parser import CONTRACT_DROPPED_TAGS

    from hos_ares_core.routes.tagging import STRUCTURAL_TAGS
    from hos_ares_core.stream import SELECTOR_TAGS
    from hos_ares_oracles import ALL_ORACLES
    from hos_ares_oracles.auth.checks import NEEDS_SEQUENCE, UNMEASURABLE

    tags_of = {
        c.meta.id: tuple(str(t) for t in getattr(c.meta, "selector_tags", ()))
        for c in ALL_ORACLES
    }
    dropped = set(CONTRACT_DROPPED_TAGS)
    limits: dict[str, list[tuple[str, str]]] = {
        "needs_sequence": sorted(NEEDS_SEQUENCE.items()),
        "unmeasurable": sorted(UNMEASURABLE.items()),
        "discoverable": sorted(
            (oid, "选择器 " + ", ".join(f"`{t}`" for t in tags) + " 不在契约字表，"
             "但结构发现（HTML 响应）能推出来——只能用 `--discover`")
            for oid, tags in tags_of.items()
            if tags and not (set(tags) & SELECTOR_TAGS) and set(tags) & set(STRUCTURAL_TAGS)
        ),
        "unreachable": sorted(
            (oid, "选择器 " + ", ".join(f"`{t}`" for t in tags) + " 既不在契约字表、结构也推不出来")
            for oid, tags in tags_of.items()
            if tags and not (set(tags) & SELECTOR_TAGS) and not (set(tags) & set(STRUCTURAL_TAGS))
        ),
        "contract_dropped": sorted(
            (oid, "选择器 " + ", ".join(f"`{t}`" for t in tags) + " 被契约解析器丢弃")
            for oid, tags in tags_of.items()
            if tags and set(tags) <= dropped
        ),
    }
    return {kind: rows for kind, rows in limits.items() if rows}


def limits_block(limits: Mapping[str, Sequence[tuple[str, str]]]) -> list[str]:
    """渲染成 markdown 行；空输入返回空列表（不留空标题）。"""
    if not limits:
        return []
    out = [
        "### 能力边界（这些判定这一轮**不可能**给出结论）",
        "",
        "| 类别 | 判定 | 原因 |",
        "|---|---|---|",
    ]
    for kind, rows in limits.items():
        for check_id, why in rows:
            detail = str(why).replace("|", chr(92) + "|")
            out.append(f"| {_LABELS.get(kind, kind)} | `{check_id}` | {detail} |")
    out += ["", "上面这些**不算未覆盖**，也不算已覆盖：它们是这一轮的观测上限。", ""]
    return out


__all__ = ["capability_limits", "limits_block"]
