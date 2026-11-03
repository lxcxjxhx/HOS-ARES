"""B12.10 插件发现层测试（36 §2）。

三条 DoD：
- **score 可复现**：同一份快照，分数完全一致；
- **6h 内刷新 0 请求**：缓存未过期时一个请求都不发；
- 增量刷新只复核 `pushed_at`，不逐个查仓库（一批 ≤50）。
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from veripen_core.plugins import (
    DEFAULT_TTL_S,
    STALE_GRACE_S,
    CacheEntry,
    DiscoveryError,
    Freshness,
    Refusal,
    Result,
    SearchCache,
    apply_incremental,
    plan_incremental,
    query_hash,
    rank,
    score,
)

NOW = datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc)
DAY = 86400


def _r(rid: str, **kw) -> Result:  # noqa: ANN003
    base = dict(id=rid, kind="skill", repo=rid.split(":", 1)[-1], stars=100,
                pushed_at="2026-10-01", archived=False, license="MIT",
                topics=("security",), install={"method": "raw", "spec": "skills/x.md"})
    base.update(kw)
    return Result(**base)


def _entry(query: str = "skill:pentest", results=None, fetched_at=None) -> CacheEntry:  # noqa: ANN001
    return CacheEntry(query=query, results=list(results or [_r("gh:a/b")]),
                      fetched_at=fetched_at or NOW, ttl_s=DEFAULT_TTL_S)


# ══════════════════════════════════════════════════════════════════
# 1 score 可复现
# ══════════════════════════════════════════════════════════════════
def test_score_matches_formula() -> None:
    """36 §2.4 的公式逐项验算，防"改权重改坏了没人发现"。"""
    r = _r("gh:o/r", stars=999, pushed_at="2026-10-30")
    expected = (
        3.0 * math.log10(1000)
        + 2.0 * 1.0      # ≤90d
        + 2.0 * 1.0      # security topic
        + 1.0 * 1.0      # MIT
        + 1.0 * 1.0      # installable
    )
    assert score(r, now=NOW) == round(expected, 6)


def test_score_is_reproducible() -> None:
    """同一份快照跑两次，分数逐字相同（可复算是这层的硬要求）。"""
    snap = [_r(f"gh:o/r{i}", stars=i * 37) for i in range(50)]
    a = [(r.id, score(r, now=NOW)) for r in snap]
    b = [(r.id, score(r, now=NOW)) for r in reversed(snap)]
    assert dict(a) == dict(b)


def test_archived_and_no_license_stack_penalties() -> None:
    good = _r("gh:a/b")
    bad = _r("gh:a/b", archived=True, license=None)
    assert score(good, now=NOW) - score(bad, now=NOW) >= 5.0 + 3.0


def test_stale_beyond_two_years_is_penalised() -> None:
    old = _r("gh:a/b", pushed_at="2020-01-01")
    assert score(old, now=NOW) < score(_r("gh:a/b"), now=NOW)


def test_recency_bands() -> None:
    from veripen_core.plugins.discovery import recency_score

    assert recency_score((NOW - timedelta(days=10)).date().isoformat(), NOW) == 1.0
    assert recency_score((NOW - timedelta(days=120)).date().isoformat(), NOW) == 0.6
    assert recency_score((NOW - timedelta(days=300)).date().isoformat(), NOW) == 0.3
    assert recency_score((NOW - timedelta(days=400)).date().isoformat(), NOW) == 0.1


def test_unparsable_date_is_treated_as_ancient() -> None:
    """日期坏了按"极旧"处理——**不猜**，也不让它排前面。"""
    assert score(_r("gh:a/b", pushed_at="not-a-date"), now=NOW) < score(_r("gh:a/b"), now=NOW)


# ══════════════════════════════════════════════════════════════════
# 2 同分排序按 id，不用 stars
# ══════════════════════════════════════════════════════════════════
def test_ties_break_by_id_not_stars() -> None:
    a = _r("gh:zzz/a")
    b = _r("gh:aaa/b")
    ranked = rank([a, b], now=NOW)
    assert [r.id for r, _ in ranked] == ["gh:aaa/b", "gh:zzz/a"]


def test_rank_is_stable_under_input_shuffle() -> None:
    items = [_r(f"gh:o/r{i:02d}") for i in range(10)]
    first = [r.id for r, _ in rank(items, now=NOW)]
    second = [r.id for r, _ in rank(list(reversed(items)), now=NOW)]
    assert first == second


# ══════════════════════════════════════════════════════════════════
# 3 缓存纪律：6h 内 0 请求
# ══════════════════════════════════════════════════════════════════
def test_fresh_cache_makes_zero_requests() -> None:
    """DoD 点名：6h 内刷新 0 请求。"""
    cache = SearchCache()
    cache.put(_entry(fetched_at=NOW))
    later = NOW + timedelta(hours=6)
    got, state = cache.search("skill:pentest", now=later)
    assert state is Freshness.FRESH
    assert got  # 有结果
    assert cache.requests == 0
    # 没调用 spend() 就不可能产生请求计数——这条断言的意义就在这里


def test_ttl_is_24h_so_6h_is_well_inside() -> None:
    assert DEFAULT_TTL_S == 86400
    assert 6 * 3600 < DEFAULT_TTL_S


def test_expired_but_recent_returns_old_results_marked_stale() -> None:
    cache = SearchCache()
    cache.put(_entry(fetched_at=NOW))
    later = NOW + timedelta(seconds=DEFAULT_TTL_S + 3600)
    got, state = cache.search("skill:pentest", now=later)
    assert state is Freshness.POSSIBLY_STALE
    assert got  # 先返回旧结果，不让人干等


def test_expired_too_long_requires_full_refresh() -> None:
    cache = SearchCache()
    cache.put(_entry(fetched_at=NOW))
    later = NOW + timedelta(seconds=DEFAULT_TTL_S + STALE_GRACE_S + 60)
    with pytest.raises(Refusal, match="full_refresh_needed") as exc:
        cache.search("skill:pentest", now=later)
    assert exc.value.cached  # 旧结果仍带回，供 UI 灰显


def test_force_bypasses_freshness() -> None:
    cache = SearchCache()
    cache.put(_entry(fetched_at=NOW))
    with pytest.raises(Refusal):
        cache.search("skill:pentest", now=NOW, force=True)


def test_absent_cache_is_a_refusal_not_an_error() -> None:
    """缓存缺失与"缓存坏了"必须能分辨：混在一起就分不清省了多少请求。"""
    cache = SearchCache()
    with pytest.raises(Refusal, match="no_cache"):
        cache.search("skill:nope", now=NOW)


def test_refresh_budget_is_enforced() -> None:
    cache = SearchCache(budget_per_refresh=2)
    cache.spend(1)
    cache.spend(1)
    with pytest.raises(DiscoveryError, match="最多 2 个请求"):
        cache.spend(1)


def test_query_hash_is_normalised() -> None:
    assert query_hash("  Skill:Pentest  ") == query_hash("skill:pentest")
    assert query_hash("skill:pentest") != query_hash("skill:sqlmap")


# ══════════════════════════════════════════════════════════════════
# 4 增量刷新
# ══════════════════════════════════════════════════════════════════
def test_incremental_batch_is_capped_at_50() -> None:
    entry = _entry(results=[_r(f"gh:o/r{i:03d}") for i in range(120)])
    assert len(plan_incremental(entry, max_batch=50)) == 50


def test_incremental_skips_when_reached_end_and_small() -> None:
    e = _entry(results=[_r("gh:a/b")], fetched_at=NOW)
    e.page_cursor = {"page": 1, "reached_end": True}
    assert plan_incremental(e) == ["a/b"]


def test_apply_incremental_updates_only_three_fields() -> None:
    """批量仓库查询给不出 topics/license/install——拿不到就保留旧值，不猜。"""
    e = _entry(results=[_r("gh:a/b", stars=100, license="MIT", topics=("security",))])
    out = apply_incremental(e, {"a/b": {"stars": 500, "pushed_at": "2026-10-31",
                                        "archived": True}})
    r = out.results[0]
    assert (r.stars, r.pushed_at, r.archived) == (500, "2026-10-31", True)
    assert r.license == "MIT" and r.topics == ("security",)


def test_apply_incremental_leaves_unknown_repos_alone() -> None:
    e = _entry(results=[_r("gh:a/b"), _r("gh:c/d")])
    out = apply_incremental(e, {"a/b": {"stars": 9}})
    assert out.results[1].stars == 100


def test_apply_incremental_does_not_mutate_source() -> None:
    e = _entry()
    before = e.results[0].stars
    apply_incremental(e, {"a/b": {"stars": 9999}})
    assert e.results[0].stars == before


# ══════════════════════════════════════════════════════════════════
# 5 缓存序列化：索引结构照 36 §2.2
# ══════════════════════════════════════════════════════════════════
def test_cache_roundtrips_through_json() -> None:
    cache = SearchCache()
    cache.put(_entry(query="skill:pentest"))
    blob = cache.to_json()
    assert blob["version"] == 1
    q = blob["queries"]["skill:pentest"]
    assert set(q) == {"query_hash", "results", "fetched_at", "ttl_s",
                      "next_check_at", "page_cursor", "etag"}
    back = CacheEntry.from_json("skill:pentest", q)
    assert [r.id for r in back.results] == ["gh:a/b"]
    assert back.freshness(NOW) is Freshness.FRESH


def test_result_parse_requires_id() -> None:
    with pytest.raises(DiscoveryError, match="缺 id"):
        Result.parse({"stars": 1})


def test_index_does_not_store_score() -> None:
    """分数是算出来的，不是存下来的：存下来就会有人手改。"""
    cache = SearchCache()
    cache.put(_entry())
    blob = cache.to_json()
    assert "score" not in blob["queries"]["skill:pentest"]["results"][0]
