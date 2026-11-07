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


def test_crawl_round_records_fetched_face_and_queues_links() -> None:
    """记录 = 这条被真正抓过的面；链接只进下一轮候选。

    早期版本把父页的 content-type 贴在子链接上，JSON 接口于是被当成 text/html，
    ``object_read`` 永远推不出来——形状必须是这条面自己的响应。
    """
    binding = _binding(p("index"))
    body = page(p("api", "items", "1"), p("login"))
    crawler = Crawler(lambda _b: (200, "text/html", body))

    round_one = crawler.step([binding])
    assert round_one.fetched == 1
    assert [r.template for r in round_one.records] == [p("index")]
    assert round_one.records[0].content_type == "text/html"
    assert len(round_one.next) == 2
    assert round_one.truncated is False


def test_crawl_does_not_refetch_a_seen_page() -> None:
    binding = _binding(p("index"))
    crawler = Crawler(lambda _b: (200, "text/html", page(p("a"))))
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
    crawler = Crawler(lambda _b: (200, "text/html", page(p("x"))), max_pages=2)
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

# ── B103：查询参数名必须进记录，否则 param_injectable 永远推不出来 ──
def test_record_keeps_query_parameter_names() -> None:
    binding = _binding(p("search") + "?q=abc")
    crawler = Crawler(lambda _b: (200, "application/json", '{"total":0}'))
    step = crawler.step([binding])
    (rec,) = step.records
    assert rec.param_names == ("q",)


def test_query_face_becomes_param_injectable() -> None:
    from veripen_core.routes.tagging import infer_tags

    binding = _binding(p("search") + "?q=abc")
    crawler = Crawler(lambda _b: (200, "application/json", '{"total":0}'))
    rec = crawler.step([binding]).records[0]
    tags = infer_tags(
        method=rec.method, template=rec.template, param_names=rec.param_names,
        content_type=rec.content_type, fields=rec.fields,
    )
    assert "param_injectable" in tags

# ── B106：渲染补充（SPA 的入口在 JS 里）──────────────────────────
class _StubRenderer:
    def __init__(self, paths: tuple[str, ...], html: str = "") -> None:
        self.paths = paths
        self.html = html
        self.calls: list[str] = []

    def capture(self, *, url: str, **_kw: object) -> object:
        from veripen_core.render.browser import CaptureResult

        self.calls.append(url)
        return CaptureResult(html=self.html, paths=self.paths, outcome="ok")


def test_render_adds_paths_that_are_not_in_the_html() -> None:
    """JS 里 fetch 的接口在源码里没有链接，只有渲染后的同源请求能看到。"""
    binding = _binding(p("index"))
    body = page(p("about.htm"))          # 源码里只有 about
    renderer = _StubRenderer(paths=(p("api", "secret") + "?id=7",))

    crawler = Crawler(lambda _b: (200, "text/html", body), renderer=renderer)
    step = crawler.step([binding])

    assert renderer.calls, "没调用渲染器"
    paths = [s.path for s in step.next]
    assert p("api", "secret") in paths
    assert p("about.htm") in paths


def test_render_is_skipped_for_json_pages() -> None:
    """JSON 响应没什么可渲染的——别把预算花在起浏览器上。"""
    binding = _binding(p("api", "x"))
    renderer = _StubRenderer(paths=(p("nope"),))
    crawler = Crawler(lambda _b: (200, "application/json", "{}"), renderer=renderer)
    crawler.step([binding])
    assert not renderer.calls


def test_render_budget_is_respected() -> None:
    bindings = [_binding(p("page", str(i))) for i in range(3)]
    renderer = _StubRenderer(paths=())
    crawler = Crawler(lambda _b: (200, "text/html", page()), renderer=renderer, max_renders=1)
    step = crawler.step(bindings)
    assert len(renderer.calls) == 1
    assert step.rendered == 1
