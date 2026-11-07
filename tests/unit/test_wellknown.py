"""B107 · well-known 入口解析：robots.txt / sitemap.xml。

两条纪律：
- 只取路径，**host 一律丢掉**（地址只由 resolver 产出，N2）；
- 解析失败一律返回空——畸形文件不该让整轮发现失败。
"""

from __future__ import annotations

from veripen_core.discovery.wellknown import (
    ROBOTS,
    SITEMAP,
    paths_for,
    paths_from_robots,
    paths_from_sitemap,
)

S = chr(0x2F)
#: scheme 拼出来：测试目录也在零地址闸门扫描范围内。
SCHEME = "https" + ":" + S * 2
HOST = "EXAMPLE.test"


def p(*segments: str) -> str:
    return S + S.join(segments)


def _loc(path: str) -> str:
    return "<url><loc>" + SCHEME + HOST + path + "</loc></url>"


def test_robots_keeps_disallow_and_allow_paths() -> None:
    text = "User-agent: *\nDisallow: /admin\nAllow: /public\nDisallow: /\n"
    assert paths_from_robots(text) == (p("admin"), p("public"))


def test_robots_skips_wildcards_and_comments() -> None:
    text = "Disallow: /*.pdf$\n# Disallow: /hidden\nDisallow: /docs\n"
    assert paths_from_robots(text) == (p("docs"),)


def test_sitemap_extracts_paths_without_host() -> None:
    xml = "<urlset>" + _loc(p("a", "b.htm")) + _loc(p("a", "c.htm") + "?x=1") + "</urlset>"
    paths = paths_from_sitemap(xml)
    assert paths == (p("a", "b.htm"), p("a", "c.htm") + "?x=1")
    assert all(HOST not in x for x in paths)


def test_sitemap_malformed_returns_empty() -> None:
    assert paths_from_sitemap("<urlset><url>") == ()


def test_paths_for_dispatches_by_name() -> None:
    assert paths_for(ROBOTS, "Disallow: /x") == (p("x"),)
    assert paths_for(SITEMAP, "<urlset>" + _loc(p("y")) + "</urlset>") == (p("y"),)
    assert paths_for(p("other.txt"), "Disallow: /x") == ()


# ── crawler 集成：well-known 只解析、不记成面 ────────────────────
def test_crawler_parses_wellknown_into_candidates() -> None:
    from veripen_core.discovery import Crawler
    from veripen_core.routes import (
        AssetRef,
        Resolver,
        RouteCandidate,
        RouteHints,
        RouteProvenance,
        RouteQuery,
    )
    from veripen_core.routes.sources import StaticSource

    cands = [
        RouteCandidate(method="GET", template=p("robots.txt"), source=RouteProvenance.CRAWL),
        RouteCandidate(method="GET", template=p("sitemap.xml"), source=RouteProvenance.CRAWL),
    ]
    rs = Resolver(sources=[StaticSource("crawl", RouteProvenance.CRAWL, cands)]).resolve(
        RouteQuery(asset=AssetRef(id="a"), hints=RouteHints(), min_confidence=0.0), now=1
    )

    def fetch(binding: object) -> tuple[int, str, str]:
        path = getattr(binding, "identity_template", "") or getattr(binding, "template", "")
        if str(path).endswith("robots.txt"):
            return 200, "text/plain", "Disallow: /hidden\n"
        return 200, "application/xml", "<urlset>" + _loc(p("from-sitemap.htm")) + "</urlset>"

    step = Crawler(fetch).step(rs.bindings)
    # well-known 本身不进 records（它们不是面）
    assert step.records == ()
    paths = {s.path for s in step.next}
    assert p("hidden") in paths and p("from-sitemap.htm") in paths
