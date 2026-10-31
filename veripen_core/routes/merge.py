"""合并与结构键（27 §3.4 / §3.6）。

structural_key 五个维度全部参与：method、归一模板、参数名集合、auth_requirement、body 形状。
归一**不做大小写**（RFC 3986 path 区分大小写，L0-12）。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .confidence import compute_confidence, primary_source, source_rank
from .types import RouteCandidate

_PARAM_SEG = re.compile(r"\{[^}]*\}")
_PCT = re.compile(r"%[0-9A-Fa-f]{2}")
_SLASH = chr(0x2F)  # 路径分隔符；用 chr 避免出现“以 / 开头的字面量”（R-PATH 只抓地址，不抓分隔符）


def normalize_template(template: str) -> str:
    """参数段归一为 ``{p}``；末尾斜杠归一；百分号编码统一小写。**不改大小写**。"""
    t = template.strip()
    t = _PCT.sub(lambda m: m.group(0).lower(), t)
    t = _PARAM_SEG.sub("{p}", t)
    if len(t) > 1 and t.endswith(_SLASH):
        t = t.rstrip(_SLASH) or _SLASH
    return t


def extract_param_names(template: str) -> tuple[str, ...]:
    return tuple(m.group(0)[1:-1] for m in _PARAM_SEG.finditer(template))


def template_sha256(template: str) -> str:
    return hashlib.sha256(normalize_template(template).encode("utf-8")).hexdigest()


def structural_key(
    method: str,
    template: str,
    param_names: tuple[str, ...],
    auth_requirement: str | None,
    body_shape_sha256: str,
) -> str:
    payload = "|".join(
        [
            method.upper(),
            normalize_template(template),
            ",".join(sorted(set(param_names))),
            auth_requirement or "none",
            body_shape_sha256 or "nobody",
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def opaque16(key: str) -> str:
    return key[:16]


def _provisional(c: RouteCandidate) -> float:
    """单候选、未合并时的置信（仅用于选 winner，不写回最终 confidence）。"""
    return compute_confidence((c.source.value,), c.verifications, c.param_ambiguity)


def merge_by_structural_key(candidates: list[RouteCandidate]) -> list[RouteCandidate]:
    """按 structural_key 分组；组内选 winner；confidence **重算**（不取 max）。"""
    groups: dict[str, list[RouteCandidate]] = {}
    for c in candidates:
        names = c.param_names or extract_param_names(c.template)
        c.param_names = tuple(names)
        c.structural_key = structural_key(
            c.method, c.template, c.param_names, c.auth_requirement, c.body_shape_sha256
        )
        c.template_sha256 = template_sha256(c.template)
        groups.setdefault(c.structural_key, []).append(c)

    merged: list[RouteCandidate] = []
    for key in sorted(groups):  # 按 key 排序遍历，不依赖输入顺序
        group = groups[key]
        winner = max(
            group,
            key=lambda c: (_provisional(c), -source_rank(c.source.value), c.template_sha256),
        )
        sources_all = tuple(sorted({c.source.value for c in group}))
        winner.sources_all = sources_all
        winner.verifications = max(c.verifications for c in group)
        winner.primary_source = primary_source(sources_all)
        winner.confidence = compute_confidence(
            sources_all, winner.verifications, winner.param_ambiguity
        )
        merged.append(winner)
    return merged


def sort_key(b: Any) -> tuple[float, int, str]:
    return (-b.confidence, source_rank(b.source), b.template_sha256)


def canonicalize_bindings(bindings: list[Any]) -> None:
    """统一排序（唯一排序规范，27 §3.6），原地。"""
    bindings.sort(key=sort_key)


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
