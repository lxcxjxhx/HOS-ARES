"""契约层（B8）：OpenAPI → OperationSet → RouteCandidate。

**故意不导出任何"发请求"的东西**：本包若能发请求，就会有人拿它去"验证路径"。
`scripts/lint/netimports.py` 把本目录列进禁止 import 网络库的范围（19 §8 末条）。
"""

from __future__ import annotations

from .parser import (
    SELF_REF_WORDS,
    TAG_VOCAB,
    ContractError,
    ContractHostOutOfScope,
    ContractTooLarge,
    Operation,
    OperationSet,
    ParamShape,
    TagAssignment,
    UnknownTag,
    normalize_template,
    parse_openapi,
    structural_key,
)
from .planner import PlanResult, plan

__all__ = [
    "ContractError",
    "ContractHostOutOfScope",
    "ContractTooLarge",
    "Operation",
    "OperationSet",
    "ParamShape",
    "PlanResult",
    "SELF_REF_WORDS",
    "TAG_VOCAB",
    "TagAssignment",
    "UnknownTag",
    "normalize_template",
    "parse_openapi",
    "plan",
    "structural_key",
]
