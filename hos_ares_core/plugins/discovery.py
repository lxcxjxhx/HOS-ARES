"""B12.10 · 插件发现层（36 §2）：搜索、缓存、增量刷新、可复算排序。

这批只有四个要点，但每一个都容易被做成"看起来对"：

1. **score 必须可复算**：给定同一份 results 快照，分数完全一致 → 单测锁定；
2. **同分按 id 字典序**，不用 stars（避免同星数抖动）；
3. **增量刷新三分支**：未过期 0 请求 / 过期不久先返回旧结果 + 后台补 / 过期太久全量；
4. **6h 内刷新 0 请求**——缓存纪律不是体验优化，是**不给自己招封禁**。

排序里刻意没有"使用率"：GitHub 不给这个数（clone 数不公开），
能拿的只有 stars / pushed_at / topics。**用 recency + stars 代理，但文案里不吹使用率。**
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

#: 36 §2.3 的三档 TTL
DEFAULT_TTL_S = 86400
STALE_GRACE_S = 7 * 86400

#: 排序权重（36 §2.4 逐项落地，改一处就要同步单测）
W_STARS = 3.0
W_RECENCY = 2.0
W_TOPIC = 2.0
W_LICENSE = 1.0
W_INSTALLABLE = 1.0
P_ARCHIVED = 5.0
P_NO_LICENSE = 3.0
P_STALE_2Y = 2.0

#: 命中即算 topic_hit 的主题（大小写不敏感）
SECURITY_TOPICS: frozenset[str] = frozenset(
    {"security", "pentest", "offensive", "redteam", "vulnerability", "exploit"}
)


class DiscoveryError(RuntimeError):
    code = "VP-8010"


class Refusal(Exception):
    """**不是错误**：本层在告诉调用方"现在不该发请求"。

    与 ``DiscoveryError`` 分开，是因为"缓存命中"和"缓存坏了"必须能分辨——
    混在一起，统计里就分不清"省了多少请求"和"坏了多少次"。
    """

    def __init__(self, reason: str, *, cached: Sequence["Result"] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.cached = list(cached or ())


class Freshness(str, Enum):
    FRESH = "cached"                 # 未过期
    POSSIBLY_STALE = "possibly_stale"  # 过期但不久
    ABSENT = "absent"                # 没有缓存


@dataclass(frozen=True)
class Result:
    """一条搜索结果。**没有 score 字段**：分数是算出来的，不是存下来的。"""

    id: str
    kind: str
    repo: str
    stars: int
    pushed_at: str  # ISO 日期，只到天
    archived: bool = False
    license: str | None = None
    topics: tuple[str, ...] = ()
    install: Mapping[str, str] | None = None

    @classmethod
    def parse(cls, raw: Mapping[str, Any]) -> "Result":
        rid = str(raw.get("id") or "")
        if not rid:
            raise DiscoveryError(f"结果缺 id：{raw}")
        return cls(
            id=rid,
            kind=str(raw.get("kind") or "skill"),
            repo=str(raw.get("repo") or ""),
            stars=int(raw.get("stars") or 0),
            pushed_at=str(raw.get("pushed_at") or ""),
            archived=bool(raw.get("archived") or False),
            license=(str(raw["license"]) if raw.get("license") else None),
            topics=tuple(str(t) for t in (raw.get("topics") or ())),
            install=dict(raw["install"]) if raw.get("install") else None,
        )


# ══════════════════════════════════════════════════════════════════
# 排序：可复算
# ══════════════════════════════════════════════════════════════════
def _days_since(pushed_at: str, now: datetime) -> int:
    """``pushed_at`` 只到天。解析失败按"极旧"处理——**不猜**。"""
    try:
        d = datetime.fromisoformat(pushed_at).replace(tzinfo=timezone.utc)
    except ValueError:
        return 10_000
    return max(0, (now - d).days)


def recency_score(pushed_at: str, now: datetime) -> float:
    days = _days_since(pushed_at, now)
    if days <= 90:
        return 1.0
    if days <= 180:
        return 0.6
    if days <= 365:
        return 0.3
    return 0.1


def topic_hit(r: Result) -> float:
    return 1.0 if any(t.lower() in SECURITY_TOPICS for t in r.topics) else 0.0


def has_license(r: Result) -> bool:
    return bool(r.license and r.license.strip().lower() not in ("", "none", "noassertion"))


def installable(r: Result) -> bool:
    return bool(r.install and r.install.get("method") and r.install.get("spec"))


def score(r: Result, *, now: datetime) -> float:
    """36 §2.4 的公式。**纯函数**：只依赖 result 与 now。

    注意 ``archived`` 与 ``no_license`` 会**同时**扣分（归档的通常也无明确许可），
    这是刻意叠加：两者各自都是"不要装"的强信号。
    """
    s = W_STARS * math.log10(r.stars + 1)
    s += W_RECENCY * recency_score(r.pushed_at, now)
    s += W_TOPIC * topic_hit(r)
    s += W_LICENSE * (1.0 if has_license(r) else 0.0)
    s += W_INSTALLABLE * (1.0 if installable(r) else 0.0)
    if r.archived:
        s -= P_ARCHIVED
    if not has_license(r):
        s -= P_NO_LICENSE
    if _days_since(r.pushed_at, now) > 730:
        s -= P_STALE_2Y
    return round(s, 6)


def rank(results: Iterable[Result], *, now: datetime) -> list[tuple[Result, float]]:
    """按 score 降序；**同分按 id 字典序**（不用 stars：同星数会抖）。"""
    scored = [(r, score(r, now=now)) for r in results]
    scored.sort(key=lambda pair: (-pair[1], pair[0].id))
    return scored


# ══════════════════════════════════════════════════════════════════
# 缓存与三分支刷新
# ══════════════════════════════════════════════════════════════════
def query_hash(query: str) -> str:
    norm = " ".join(query.strip().lower().split())
    return "sha256:" + hashlib.sha256(norm.encode("utf-8")).hexdigest()


@dataclass
class CacheEntry:
    query: str
    results: list[Result] = field(default_factory=list)
    fetched_at: datetime | None = None
    ttl_s: int = DEFAULT_TTL_S
    page_cursor: dict[str, Any] = field(default_factory=lambda: {"page": 1, "reached_end": False})
    etag: str = ""

    @property
    def qhash(self) -> str:
        return query_hash(self.query)

    def next_check_at(self) -> datetime:
        base = self.fetched_at or datetime.fromtimestamp(0, tz=timezone.utc)
        return base + timedelta(seconds=self.ttl_s)

    def freshness(self, now: datetime) -> Freshness:
        if self.fetched_at is None:
            return Freshness.ABSENT
        return Freshness.FRESH if now < self.next_check_at() else Freshness.POSSIBLY_STALE

    def to_json(self) -> dict[str, Any]:
        return {
            "query_hash": self.qhash,
            "results": [
                {
                    "id": r.id, "kind": r.kind, "repo": r.repo, "stars": r.stars,
                    "pushed_at": r.pushed_at, "archived": r.archived,
                    "license": r.license, "topics": list(r.topics),
                    "install": dict(r.install) if r.install else None,
                }
                for r in self.results
            ],
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
            "ttl_s": self.ttl_s,
            "next_check_at": self.next_check_at().isoformat() if self.fetched_at else None,
            "page_cursor": dict(self.page_cursor),
            "etag": self.etag,
        }

    @classmethod
    def from_json(cls, query: str, raw: Mapping[str, Any]) -> "CacheEntry":
        at = raw.get("fetched_at")
        return cls(
            query=query,
            results=[Result.parse(x) for x in (raw.get("results") or ())],
            fetched_at=datetime.fromisoformat(at) if at else None,
            ttl_s=int(raw.get("ttl_s") or DEFAULT_TTL_S),
            page_cursor=dict(raw.get("page_cursor") or {"page": 1, "reached_end": False}),
            etag=str(raw.get("etag") or ""),
        )


class SearchCache:
    """``search_index.json`` 的内存视图。**请求数由这里把关，不靠调用方自觉。**"""

    def __init__(self, *, budget_per_refresh: int = 2) -> None:
        self._entries: dict[str, CacheEntry] = {}
        self.requests = 0
        self.budget_per_refresh = budget_per_refresh

    def get(self, query: str) -> CacheEntry | None:
        return self._entries.get(query_hash(query))

    def put(self, entry: CacheEntry) -> None:
        self._entries[entry.qhash] = entry

    def freshness(self, query: str, now: datetime) -> Freshness:
        e = self.get(query)
        return Freshness.ABSENT if e is None else e.freshness(now)

    def search(
        self, query: str, *, now: datetime, force: bool = False
    ) -> tuple[list[tuple[Result, float]], Freshness]:
        """三分支（36 §2.3）。返回 ``(排序结果, 新鲜度标记)``。

        过期但不久 → **先返回旧结果**并标 ``possibly_stale``；后台补第一页是调用方的事，
        本函数只负责"不让用户干等，也不让请求失控"。
        """
        e = self.get(query)
        if e is None:
            raise Refusal("no_cache")
        state = e.freshness(now)
        if state is Freshness.FRESH and not force:
            return rank(e.results, now=now), Freshness.FRESH

        age = (now - (e.fetched_at or now)).total_seconds()
        if not force and age < DEFAULT_TTL_S + STALE_GRACE_S:
            return rank(e.results, now=now), Freshness.POSSIBLY_STALE
        # 过期太久或强制刷新：调用方必须走全量，且受预算约束
        raise Refusal("full_refresh_needed", cached=[r for r, _ in rank(e.results, now=now)])

    def spend(self, n: int = 1) -> None:
        if self.requests + n > self.budget_per_refresh:
            raise DiscoveryError(
                f"一次刷新最多 {self.budget_per_refresh} 个请求（36 §2.3），"
                f"已用 {self.requests}，本次要 {n}"
            )
        self.requests += n

    def reset_budget(self) -> None:
        self.requests = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "version": 1,
            "queries": {e.query: e.to_json() for e in self._entries.values()},
        }


# ══════════════════════════════════════════════════════════════════
# 增量刷新：比对 pushed_at（批量，不逐个查）
# ══════════════════════════════════════════════════════════════════
def plan_incremental(entry: CacheEntry, *, max_batch: int = 50) -> list[str]:
    """要复核 ``pushed_at`` 的 repo 列表。**一批 50 个走 GraphQL**（36 §2.3）。

    返回空列表 = 没有要复核的（例如已 ``reached_end``），调用方应当 0 请求直接复用。
    """
    if not entry.results:
        return []
    if entry.page_cursor.get("reached_end") and len(entry.results) < max_batch:
        return [r.repo for r in entry.results if r.repo]
    return [r.repo for r in entry.results[:max_batch] if r.repo]


def apply_incremental(
    entry: CacheEntry, updates: Mapping[str, Mapping[str, Any]]
) -> CacheEntry:
    """把批量查询回来的 ``{repo: {stars, pushed_at, archived}}`` 合并进缓存。

    **只更新这三个字段**：其他字段（topics/license/install）来自搜索结果页，
    批量仓库查询给不出来——拿不到就保留旧值，不猜。
    """
    merged: list[Result] = []
    for r in entry.results:
        u = updates.get(r.repo)
        if not u:
            merged.append(r)
            continue
        merged.append(
            Result(
                id=r.id, kind=r.kind, repo=r.repo,
                stars=int(u.get("stars", r.stars)),
                pushed_at=str(u.get("pushed_at", r.pushed_at)),
                archived=bool(u.get("archived", r.archived)),
                license=r.license, topics=r.topics, install=r.install,
            )
        )
    return CacheEntry(
        query=entry.query, results=merged, fetched_at=entry.fetched_at,
        ttl_s=entry.ttl_s, page_cursor=dict(entry.page_cursor), etag=entry.etag,
    )


__all__ = [
    "DEFAULT_TTL_S",
    "SECURITY_TOPICS",
    "STALE_GRACE_S",
    "CacheEntry",
    "DiscoveryError",
    "Freshness",
    "Refusal",
    "Result",
    "SearchCache",
    "apply_incremental",
    "has_license",
    "installable",
    "plan_incremental",
    "query_hash",
    "rank",
    "recency_score",
    "score",
    "topic_hit",
]
