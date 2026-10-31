"""B5 靶场路由：**测试夹具里唯一允许出现模板字面量的地方**。

真实运行时，模板来自契约（B8）或观测（HAR/capsule）；这里是把
``tests/lab/app.assets_json()`` 直接转成 ``RouteBinding``，好让 oracle 只"看得见绑定"。
"""

from __future__ import annotations

from typing import Iterable

from veripen_core.routes import (
    AssetRef,
    Resolver,
    RouteHints,
    RouteProvenance,
    RouteQuery,
    RouteSet,
    StaticSource,
    build_binding,
    template_sha256,
)
from veripen_core.routes.types import RouteCandidate

from .app import assets_json

ASSET = "web-01"
NOW = 1_000


def candidates(tags: Iterable[str] | None = None) -> list[RouteCandidate]:
    wanted = set(tags or ())
    out: list[RouteCandidate] = []
    for op in assets_json()["operations"]:
        if wanted and not wanted & set(op["tags"]):
            continue
        out.append(
            RouteCandidate(
                method=op["method"],
                template=op["template"],
                param_names=tuple(p["name"] for p in op["params"]),
                source=RouteProvenance.CONTRACT,
                response_shape={"semantic_tags": op["tags"]},
            )
        )
    return out


def routeset(tags: Iterable[str] | None = None, min_confidence: float = 0.70) -> RouteSet:
    q = RouteQuery(
        asset=AssetRef(id=ASSET),
        hints=RouteHints(),
        min_confidence=min_confidence,
    )
    return Resolver(sources=[StaticSource("contract", RouteProvenance.CONTRACT, candidates(tags))]).resolve(
        q, now=NOW
    )


def binding_for(tag: str, template: str | None = None) -> object:
    """按标签取一条绑定。``template`` 仅在测试需要"改版"时用于构造原始候选。"""
    rs = routeset([tag])
    if not rs.bindings:
        raise LookupError(f"没有 {tag} 标签的绑定")
    if template is None:
        return rs.bindings[0]
    for b in rs.bindings:
        if b.template == template:
            return b
    raise LookupError(f"{tag} 下没有模板 {template}")


def make_binding(ref: str, template: str, param_names: tuple[str, ...] = (), method: str = "GET") -> object:
    """手工造一条绑定（仅测试夹具；生产里只有 resolver 能构造）。"""
    return build_binding(
        ref=ref,
        source=RouteProvenance.CONTRACT.value,
        sources_all=(RouteProvenance.CONTRACT.value,),
        confidence=0.9,
        template=template,
        method=method,
        param_names=param_names,
        auth_requirement=None,
        body_shape_sha256="nobody",
        structural_key="k" * 16,
        template_sha256=template_sha256(template),
        ttl_s=900,
        verifications=0,
        usable=True,
    )
