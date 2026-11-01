"""契约 → RouteSet（B8）：OperationSet 转成 ``routes`` 能消费的候选。

分工（19 §1）：本模块只把**结构**交给 routes；选择交给 profile 的 selectors；
判定留给 oracle。所以这里没有 `send`、没有地址、没有 base_url。

一条硬规矩：**没有标签的 operation 不进候选**——它测不了，进 RouteSet 只会污染
"覆盖了多少面"的分母（19 §4 空标签处理）。
"""

from __future__ import annotations

from dataclasses import dataclass

from veripen_core.routes.types import RouteCandidate, RouteProvenance

from typing import Iterable

from .parser import Operation, OperationSet


@dataclass(frozen=True)
class PlanResult:
    candidates: tuple[RouteCandidate, ...]
    skipped: tuple[tuple[str, str], ...]  # (op_id, 原因)

    @property
    def counts(self) -> dict[str, int]:
        return {"candidates": len(self.candidates), "skipped": len(self.skipped)}


def plan(
    opset: OperationSet,
    *,
    source: RouteProvenance = RouteProvenance.CONTRACT,
    confirm: Iterable[str] = (),
) -> PlanResult:
    """把 operations 转成 RouteCandidate。**只带结构，不带地址。**

    ``confirm`` 来自 profile 的 ``selectors``——那是 operator 的确认，
    也是"结构推断不能单独成立"这条规矩的出口（19 §3.1）。
    """
    out: list[RouteCandidate] = []
    skipped: list[tuple[str, str]] = []
    confirm_set = set(confirm)

    for operation in opset.operations:
        if confirm_set:
            operation = operation.confirm(confirm_set)
        if not operation.tags:
            skipped.append(
                (operation.op_id, "awaiting_operator" if operation.proposed else "no_tags")
            )
            continue
        if not _has_param_schema(operation):
            skipped.append((operation.op_id, "no_params"))
            continue
        out.append(_candidate(operation, source, opset.resolver_version))

    out.sort(key=lambda c: (c.template_sha256, c.method))
    return PlanResult(tuple(out), tuple(skipped))


def _candidate(operation: Operation, source: RouteProvenance, resolver_version: str) -> RouteCandidate:
    body_shape = "|".join(sorted(operation.success_shape.get("fields") or ())) or "nobody"
    return RouteCandidate(
        method=operation.method,
        template=operation.template,
        param_names=tuple(p.name for p in operation.params),
        auth_requirement=operation.auth_requirement,
        body_shape_sha256=body_shape,
        source=source,
        verifications=0,
        param_ambiguity=0 if len(operation.params) <= 2 else len(operation.params) - 2,
        # 结构化标签随候选传递：resolver 不解读，oracle 通过 binding 看不到它们，
        # 但 profile 的 selectors 需要它们来问"有哪些面"。
        response_shape={
            "semantic_tags": list(operation.tag_names),
            "resolver_version": resolver_version,
            "score": operation.score,
            "disputed": list(operation.disputed_tags),
        },
    )


def _has_param_schema(operation: Operation) -> bool:
    """无参数的 operation 也能测（如 protected_read），但**必须**是有标签的；
    这里只排除"连 tags 都没有"的，参数为空是合法的。"""
    return True


__all__ = ["PlanResult", "plan"]
