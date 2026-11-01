"""解析器：**全仓唯一构造 RouteBinding 的地方**（N2）。

流程严格照 27 §4.1 的参考伪码：
单飞 → 缓存 → 逐源采集（失败只跳过）→ 合并 → 标可用 → 排序 → 定 ref → 内容寻址。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Sequence

from .cache import RouteSetCache, SingleFlight, cache_key
from .confidence import source_rank
from .merge import merge_by_structural_key, opaque16, sort_key
from .sources import Source, SourceError, default_sources
from .types import (
    DiscoveryReason,
    RouteBinding,
    RouteCandidate,
    RouteQuery,
    RouteSet,
    RouteStale,
    Unresolved,
    UnresolvedReason,
)

RESOLVER_VERSION = "1.0.0"
TRUNCATE_LIMIT = 5


def build_binding(
    *,
    ref: str,
    source: str,
    sources_all: tuple[str, ...],
    confidence: float,
    template: str,
    method: str,
    param_names: tuple[str, ...],
    auth_requirement: str | None,
    body_shape_sha256: str,
    structural_key: str,
    template_sha256: str,
    ttl_s: int,
    verifications: int,
    usable: bool,
    semantic_tags: tuple[str, ...] = (),
) -> RouteBinding:
    """★ 唯一构造点。其余模块只能接收 RouteBinding，不许自己造。"""
    return RouteBinding(
        ref=ref,
        source=source,
        sources_all=sources_all,
        confidence=confidence,
        template=template,
        method=method,
        param_names=param_names,
        auth_requirement=auth_requirement,
        body_shape_sha256=body_shape_sha256,
        structural_key=structural_key,
        template_sha256=template_sha256,
        ttl_s=ttl_s,
        verifications=verifications,
        usable=usable,
        semantic_tags=semantic_tags,
    )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical(obj: object) -> str:
    """稳定序列化（键排序 + 紧凑分隔符）。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hashable(rs: RouteSet) -> dict[str, Any]:
    """进哈希的字段：**时间不进哈希**（同 capsule 的 path_rendered 同理）。

    内容寻址必须与时钟无关，否则同一次解析在不同时刻会得到不同 sha，
    N6（三轮 routeset 一致）与录制回放（L0-24）都不可能成立。
    """
    payload = rs.without_sha()
    payload.pop("created_at", None)
    payload.pop("expires_at", None)
    return payload


def _bindings_from(cands: Sequence[RouteCandidate], q: RouteQuery) -> list[RouteBinding]:
    out: list[RouteBinding] = []
    for c in cands:
        src = getattr(c.source, "value", c.source)
        out.append(
            build_binding(
                ref=f"route://{src}/{opaque16(c.structural_key)}",
                source=src,
                sources_all=c.sources_all,
                confidence=c.confidence,
                template=c.template,
                method=c.method,
                param_names=c.param_names,
                auth_requirement=c.auth_requirement,
                body_shape_sha256=c.body_shape_sha256,
                structural_key=c.structural_key,
                template_sha256=c.template_sha256,
                ttl_s=q.ttl_s,
                verifications=c.verifications,
                usable=c.confidence >= q.min_confidence,
                semantic_tags=tuple(c.response_shape.get("semantic_tags") or ()),
            )
        )
    out.sort(key=sort_key)
    return out


class RediscoveryBudget:
    """每任务重发现配额（27 §4）：超了即 ``QUOTA_EXCEEDED``，不无限重试。"""

    def __init__(self, max_rediscovery: int = 3) -> None:
        self.max = max_rediscovery
        self.used = 0

    def spend(self) -> None:
        from .types import QuotaExceeded

        if self.used >= self.max:
            raise QuotaExceeded(f"重发现配额用尽（{self.used}/{self.max}）")
        self.used += 1


def assert_fresh(rs: RouteSet, now: int) -> None:
    """TTL 校验：过期即 ``RouteStale``——**不许重放旧绑定**（L0-26）。"""
    if rs.expired(now):
        raise RouteStale(f"绑定集已过期（expires_at={rs.expires_at}, now={now}）")


def truncate_for_selector(
    bindings: Sequence[RouteBinding],
    selector: str,
    limit: int = TRUNCATE_LIMIT,
) -> tuple[list[RouteBinding], Unresolved | None]:
    """一个 selector 的命中裁决：不裁决，只排序 + 限量（27 §3.5）。"""
    ordered = sorted(bindings, key=sort_key)
    if not ordered:
        return [], Unresolved(
            selector=selector,
            reason=UnresolvedReason.NO_CANDIDATE.value,
            detail={"hits": 0},
        )
    if len(ordered) > limit:
        kept = ordered[:limit]
        return kept, Unresolved(
            selector=selector,
            reason=UnresolvedReason.TRUNCATED.value,
            detail={"truncated": len(ordered) - limit, "hits": len(ordered)},
        )
    return ordered, None


def cache_key_for(q: RouteQuery, profile_hash: str = "") -> str:
    query_hash = _sha256(_canonical(_query_payload(q)))
    return cache_key(q.asset.id, query_hash, profile_hash, RESOLVER_VERSION)


def _query_payload(q: RouteQuery) -> dict[str, Any]:
    return {
        "asset": q.asset.id,
        "kind": q.asset.kind,
        "tag": q.hints.semantic_tag,
        "param_kind": q.hints.param_kind,
        "param_name_hint": q.hints.param_name_hint,
        "auth_requirement": q.hints.auth_requirement,
        "auth": q.auth,
        "reason": q.reason.value,
        "ttl_s": q.ttl_s,
        "min_confidence": q.min_confidence,
    }


class Resolver:
    """解析器实例：持有缓存与单飞，可注入来源（测试不联网）。"""

    def __init__(
        self,
        cache: RouteSetCache | None = None,
        single_flight: SingleFlight | None = None,
        sources: Iterable[Source] | None = None,
    ) -> None:
        self.cache = cache or RouteSetCache()
        self.sf = single_flight or SingleFlight()
        self._sources = list(sources) if sources is not None else None

    def resolve(self, q: RouteQuery, now: int, profile_hash: str = "") -> RouteSet:
        key = cache_key_for(q, profile_hash)
        return self.sf.do(key, lambda: self._resolve_uncached(q, now, key))

    def _resolve_uncached(self, q: RouteQuery, now: int, key: str) -> RouteSet:
        hit = self.cache.get(key)
        if hit is not None:
            fresh = not hit.expired(now)
            short_ok = self.cache.is_short(key) and self.cache.short_alive(key, now)
            if q.reason is DiscoveryReason.INIT and (fresh or short_ok):
                return hit

        sources = list(self._sources) if self._sources is not None else default_sources(q)
        candidates: list[RouteCandidate] = []
        failed: list[str] = []
        for src in sources:
            try:
                candidates.extend(src.collect(q))
            except SourceError:
                failed.append(src.name)

        query_hash = _sha256(_canonical(_query_payload(q)))

        if not candidates:
            rs = RouteSet(
                resolver_version=RESOLVER_VERSION,
                asset_id=q.asset.id,
                query_hash=query_hash,
                created_at=now,
                expires_at=now + q.ttl_s,
                sources_used=(),
                sources_failed=tuple(sorted(failed)),
                unresolved=(
                    Unresolved(
                        selector=q.hints.semantic_tag or "",
                        reason=UnresolvedReason.NO_CANDIDATE.value,
                        detail={"sources_failed": sorted(failed)},
                    ),
                ),
                bindings=[],
            )
            rs.routeset_sha256 = _sha256(_canonical(_hashable(rs)))
            self.cache.put_short(key, rs, now)  # 失败短缓存 60s，防连打
            return rs

        merged = merge_by_structural_key(candidates)
        bindings = _bindings_from(merged, q)
        rs = RouteSet(
            resolver_version=RESOLVER_VERSION,
            asset_id=q.asset.id,
            query_hash=query_hash,
            created_at=now,
            expires_at=now + q.ttl_s,
            sources_used=tuple(sorted({b.source for b in bindings})),
            sources_failed=tuple(sorted(failed)),
            unresolved=tuple(_unresolved_for(q, bindings)),
            bindings=bindings,
        )
        rs.routeset_sha256 = _sha256(_canonical(_hashable(rs)))  # N6 的锚
        self.cache.put(key, rs)
        return rs


def _unresolved_for(q: RouteQuery, bindings: list[RouteBinding]) -> list[Unresolved]:
    if q.hints.semantic_tag and not bindings:
        return [
            Unresolved(
                selector=q.hints.semantic_tag,
                reason=UnresolvedReason.NO_CANDIDATE.value,
                detail={"hits": 0},
            )
        ]
    return []


def resolve_routes(
    q: RouteQuery,
    now: int,
    *,
    sources: Iterable[Source] | None = None,
    resolver: Resolver | None = None,
    profile_hash: str = "",
) -> RouteSet:
    """模块级入口（CLI / 技能同名）。"""
    r = resolver or Resolver(sources=sources)
    return r.resolve(q, now, profile_hash)


def sort_bindings(bindings: list[RouteBinding]) -> list[RouteBinding]:
    return sorted(bindings, key=sort_key)


def rank(binding: RouteBinding) -> int:
    return source_rank(binding.source)
