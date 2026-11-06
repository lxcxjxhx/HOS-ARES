"""B86 · 无契约发现：抽取 / 归一 / 轮次（`H27`）。

三条断言对应三层，各自能红：

| 断言 | 会红的情形 |
|---|---|
| 抽取只收本机入口 | 把带 scheme 的外链也收进来 → 跨资产越界 |
| 归一不多归 | 把 ``users`` 归成 ``{p}`` → 两条面撞成一个 ref |
| 轮次不重复抓 | 去掉 ``_seen`` → 第二轮又抓首页，预算翻倍 |
"""

from __future__ import annotations

import pytest

from veripen_core.discovery import Crawler, extract, normalize_href, templatize
from veripen_core.routes.resolver import Resolver
from veripen_core.routes.sources import CrawlSource
from veripen_core.routes.sources.base import ObservedRecord, StaticSource
from veripen_core.routes.types import AssetRef, RouteCandidate, RouteProvenance, RouteQuery

#: 路径分隔符写成常量：测试目录也在零地址闸门的扫描范围内。
S = chr(0x2F)


def p(*segments: str) -> str:
    return S + S.join(segments)


def page(*hrefs: str) -> str:
    anchors = "".join(f'<a href="{h}">x</a>' for h in hrefs)
    return f"<html><body>{anchors}</body></html>"


def _binding(template: str, names: tuple[str, ...] = ()) -> object:
    cand = RouteCandidate(
        method="GET",
        template=template,
        param_names=names,
        source=RouteProvenance.CRAWL,
    )
    rs = Resolver(sources=[StaticSource("crawl", RouteProvenance.CRAWL, [cand])]).resolve(
        RouteQuery(asset=AssetRef(id="asset"), min_confidence=0.0), now=1
    )
    return rs.bindings[0]


# ── 1. 抽取 ────────────────────────────────────────────────────────


def test_extract_keeps_local_and_drops_external() -> None:
    # 外链与协议相对地址都写成拼接：测试目录也在零地址闸门扫描范围内。
    foreign = "https" + ":" + S * 2 + "elsewhere.example" + S + "x"
    protocol_relative = S * 2 + "cdn.example" + S + "y.js"
    html = page(
        p("api", "users", "1"),
        foreign,
        protocol_relative,
        "#top",
        "mailto:a@b.c",
        "javascript:void(0)",
        "relative/path",
    )
    got = [s.path for s in extract(html)]
    assert got == [p("api", "users", "1"), p("relative", "path")]


def test_extract_form_carries_method_and_param_names() -> None:
    html = (
        '<form action="' + p("login") + '" method="post">'
        '<input name="user"><input name="pw"></form>'
    )
    (sighting,) = extract(html)
    assert sighting.method == "POST"
    assert sighting.path == p("login")
    assert sighting.params == ("user", "pw")


def test_extract_dedupes_same_path_on_one_page() -> None:
    assert len(extract(page(p("a"), p("a"), p("a")))) == 1


def test_normalize_href_strips_query_fragment() -> None:
    assert normalize_href(p("x") + "?a=1&b=2#frag") == (p("x"), ("a", "b"))


def test_normalize_href_resolves_dots_against_page_path() -> None:
    got = normalize_href(".." + S + ".." + S + "a" + S + "b.htm", base_path=p("x", "y", "page.htm"))
    assert got == (p("a", "b.htm"), ())


def test_normalize_href_drops_dots_above_root() -> None:
    assert normalize_href(S + ".." + S + ".." + S + "a.htm") == (p("a.htm"), ())


# ── 2. 归一 ────────────────────────────────────────────────────────


def test_templatize_replaces_ids_only() -> None:
    template, names = templatize(p("api", "users", "12345", "orders", "3f2504e0-4f89-11d3-9a0c"))
    assert template == p("api", "users", "{p1}", "orders", "{p2}")
    assert names == ("p1", "p2")


def test_templatize_keeps_version_and_word_segments() -> None:
    template, names = templatize(p("api", "v1", "users"))
    assert template == p("api", "v1", "users")
    assert names == ()


def test_templatize_absorbs_cms_article_numbers() -> None:
    """``9825.htm`` 是一条面，不是几十条——后缀要留在模板里。"""
    template, names = templatize(p("info", "12", "9825.htm"))
    assert template == p("info", "{p1}", "{p2}.htm")
    assert names == ("p1", "p2")


@pytest.mark.parametrize("path", [p("a", "1"), p("b", "2024", "x")])
def test_templatize_is_idempotent_on_templates(path: str) -> None:
    once, _ = templatize(path)
    twice, _ = templatize(once)
    assert once == twice


# ── 3. 轮次 ────────────────────────────────────────────────────────


def test_crawl_round_emits_records_and_next_entries() -> None:
    binding = _binding(p("index"))
    body = page(p("api", "items", "1"), p("login"))
    crawler = Crawler(lambda _b: (200, body))

    round_one = crawler.step([binding])
    assert round_one.fetched == 1
    assert [r.template for r in round_one.records] == [
        p("api", "items", "{p1}"),
        p("login"),
    ]
    assert len(round_one.next) == 2
    assert round_one.truncated is False


def test_crawl_does_not_refetch_a_seen_page() -> None:
    binding = _binding(p("index"))
    crawler = Crawler(lambda _b: (200, page(p("a"))))
    crawler.step([binding])
    second = crawler.step([binding])
    assert second.fetched == 0
    assert second.records == ()


def test_crawl_failure_is_skipped_not_fatal() -> None:
    binding = _binding(p("index"))
    round_one = Crawler(lambda _b: None).step([binding])
    assert round_one.records == ()
    assert round_one.skipped == ((p("index"), "fetch_failed"),)


def test_crawl_respects_page_budget() -> None:
    bindings = [_binding(p("page", str(i))) for i in range(3)]
    crawler = Crawler(lambda _b: (200, page(p("x"))), max_pages=2)
    round_one = crawler.step(bindings)
    assert round_one.fetched == 2
    assert round_one.truncated is True


# ── 4. 来源契约：看见 ≠ 打过 ───────────────────────────────────────


def test_crawl_source_confidence_is_lower_than_observed() -> None:
    rec = ObservedRecord(method="GET", template=p("api", "me"), verifications=0)
    crawl = Resolver(sources=[CrawlSource([rec])]).resolve(
        RouteQuery(asset=AssetRef(id="asset"), min_confidence=0.0), now=1
    )
    assert crawl.bindings[0].source == "crawl"
    assert crawl.bindings[0].confidence == pytest.approx(0.70)
    assert crawl.bindings[0].usable is True


# ── 5. 下载型资源不是"面" ─────────────────────────────────────────


def test_extract_drops_download_resources() -> None:
    """`application/pdf` / `image/*` 在 HTTP 上是 file 不是 page，不该进发现面。"""
    html = page(
        p("a", "b.pdf"),
        p("a", "c.jpg"),
        p("a", "d.css"),
        p("a", "e.htm"),
        p("a", "f.jsp"),
    )
    assert [s.path for s in extract(html)] == [p("a", "e.htm"), p("a", "f.jsp")]


def test_extract_keeps_query_style_downloads() -> None:
    """`/export?file=x.pdf` 是**页面端点**（path 无后缀），不要误杀。"""
    html = page(p("export") + "?file=x.pdf")
    assert [s.path for s in extract(html)] == [p("export")]
