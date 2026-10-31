"""置信度：**规则算出，不许赋值**（27 §3.3 / L0-18）。

任何地方出现 ``confidence = 0.9`` 这类字面赋值都违规；本模块只提供函数。
"""

from __future__ import annotations

from .types import RouteProvenance

BASE_CONFIDENCE: dict[str, float] = {
    RouteProvenance.CONTRACT.value: 0.90,
    RouteProvenance.OBSERVED.value: 1.00,
    RouteProvenance.CRAWL.value: 0.70,
    RouteProvenance.PROBE.value: 0.50,
    RouteProvenance.OPERATOR.value: 0.95,
}

#: 排序/选择用的来源优先级（数值小=优先）：observed>operator>contract>crawl>probe
SOURCE_RANK: dict[str, int] = {
    RouteProvenance.OBSERVED.value: 0,
    RouteProvenance.OPERATOR.value: 1,
    RouteProvenance.CONTRACT.value: 2,
    RouteProvenance.CRAWL.value: 3,
    RouteProvenance.PROBE.value: 4,
}

#: 参与“结构主语”的来源（observed 只提供佐证，不当主语，除非它独有）
STRUCTURAL_SOURCES = (
    RouteProvenance.OPERATOR.value,
    RouteProvenance.CONTRACT.value,
    RouteProvenance.CRAWL.value,
    RouteProvenance.PROBE.value,
)


def base_confidence(source: str) -> float:
    return BASE_CONFIDENCE[source]


def source_rank(source: str) -> int:
    return SOURCE_RANK[source]


def primary_source(sources_all: tuple[str, ...]) -> str:
    """结构主语：结构类来源里 base 最高者；若只有 observed，则 observed 当主语。"""
    structural = [s for s in sources_all if s in STRUCTURAL_SOURCES]
    pool = structural or list(sources_all)
    return max(pool, key=lambda s: base_confidence(s))


def compute_confidence(
    sources_all: tuple[str, ...],
    verifications: int,
    param_ambiguity: int,
) -> float:
    """base + 0.05×(源数-1) + 0.05×min(verif,2) − 0.10×ambiguity，clamp 到 [0,1]。"""
    if not sources_all:
        return 0.0
    primary = primary_source(sources_all)
    value = (
        base_confidence(primary)
        + 0.05 * (len(sources_all) - 1)
        + 0.05 * min(max(verifications, 0), 2)
        - 0.10 * max(param_ambiguity, 0)
    )
    return max(0.0, min(1.0, value))


def confidence_tier(confidence: float) -> str:
    """分档（27 §3.3 末表）：决定“能不能打”。"""
    if confidence >= 0.95:
        return "observed"
    if confidence >= 0.85:
        return "contract"
    if confidence >= 0.65:
        return "crawl"
    if confidence >= 0.45:
        return "probe"
    return "inferred"
