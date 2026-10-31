"""来源包：五个来源的实现与注册（27 §3.2）。

B1.5 落 ``contract`` 与 ``observed`` 两个真实现；
``crawl`` / ``probe`` / ``operator`` 先给空适配器（返回 []），由后续批次接线。
空适配器**不报错、不猜路径**——词表为空时 probe 直接不参与（27 §3.2 末注）。
"""

from __future__ import annotations

from ..types import RouteProvenance, RouteQuery
from .base import (
    ContractOp,
    ObservedRecord,
    Source,
    SourceError,
    StaticSource,
    body_shape_sha256,
    matches_hints,
)
from .contract import ContractSource, parse_operations, validate_contract_hosts
from .observed import ObservedSource


def empty_source(name: str, provenance: RouteProvenance) -> StaticSource:
    return StaticSource(name=name, provenance=provenance, candidates=[])


def default_sources(q: RouteQuery) -> list[Source]:  # noqa: ARG001 - 形态先定，数据由绑定注入
    """默认来源集合。真实运行时数据由 profile/bindings 注入，这里给空集不猜。"""
    return [
        ContractSource([]),
        ObservedSource([]),
        empty_source("crawl", RouteProvenance.CRAWL),
        empty_source("probe", RouteProvenance.PROBE),
        empty_source("operator", RouteProvenance.OPERATOR),
    ]


__all__ = [
    "ContractOp",
    "ContractSource",
    "ObservedRecord",
    "ObservedSource",
    "Source",
    "SourceError",
    "StaticSource",
    "body_shape_sha256",
    "default_sources",
    "empty_source",
    "matches_hints",
    "parse_operations",
    "validate_contract_hosts",
]
