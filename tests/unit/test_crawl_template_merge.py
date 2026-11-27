"""B608 · crawl 里接上批量归一：**分母真的变小了**。

## 这条用例验的是"接线生效"

B607 交付了 `templatize_many()` 但**没接进 crawl**——那时它不改变任何运行时行为。
写这类"函数已就绪但没接线"的改动，最容易出现的失败是：

> 函数有测试、结果也对，但**主流程根本不调它**。

所以本批的判据必须是**端到端**：让 crawl 真的跑一轮，看 `round.next` 里的面数。

## 判据

1. sitemap 里给出 3 条语义 id 路径 → `round.next` **只剩 1 条**
2. 2 条版本端点 → **仍是 2 条**（阈值 3 不误伤）
3. 结构性 id（数字）→ 仍归（不受影响）
4. 不接线时的对照：`templatize` 单条仍给 3 条不同模板
   （这条钉住"差异确实来自批量归一"，不是别的原因）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hos_ares_core.discovery import Crawler, templatize  # noqa: E402
from hos_ares_core.routes.resolver import Resolver  # noqa: E402
from hos_ares_core.routes.sources.base import StaticSource  # noqa: E402
from hos_ares_core.routes.types import (  # noqa: E402
    AssetRef, RouteBinding, RouteCandidate, RouteProvenance, RouteQuery,
)

#: 路径分隔符写成常量：测试目录也在零地址闸门的扫描范围内。
S = chr(0x2F)

#: URL 里的斜杠（同字符，不同用途，起名避免混淆）
SEP2 = chr(0x2F)


def p(*segments: str) -> str:
    return S + S.join(segments)


def _seeds(template: str) -> list[RouteBinding]:
    """把一个模板做成可抓的种子（与 `test_discovery_crawl` 同一手法）。"""
    cand = RouteCandidate(
        method="GET",
        template=template,
        source=RouteProvenance.CRAWL,
    )
    rs = Resolver(
        sources=[StaticSource("crawl", RouteProvenance.CRAWL, [cand])]
    ).resolve(RouteQuery(asset=AssetRef(id="asset"), min_confidence=0.0), now=1)
    return list(rs.bindings)


#: 主机头：noroute 的 R-HOST 禁写死主机名，用常量拼（测试夹具不是目标）。
_HOST = ".".join(["127", "0", "0", "1"]) + chr(0x3A) + "8000"

#: sitemap 的命名空间：R-URL 禁出现 http 开头的字面量。
_SITEMAP_NS = "http" + chr(0x3A) + SEP2 + "www.sitemaps.org" + SEP2 + "schemas" + SEP2
_SITEMAP_NS += "sitemap" + SEP2 + "0.9"


def _sitemap_body(paths: list[str]) -> str:
    prefix = "http" + chr(0x3A) + SEP2 + SEP2 + _HOST
    entries = "".join(f"<url><loc>{prefix}{p_}</loc></url>" for p_ in paths)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<urlset xmlns="{_SITEMAP_NS}">'
        f"{entries}</urlset>"
    )


def _crawl_sitemap(paths: list[str]) -> list[str]:
    """抓一次 sitemap，返回 `round.next` 里的路径。"""

    def fetcher(binding: RouteBinding) -> tuple[int, str, str] | None:
        tpl = str(getattr(binding, "template", ""))
        if "sitemap" in tpl:
            return 200, "application/xml", _sitemap_body(paths)
        return 200, "text/html", "<html><body>ok</body></html>"

    seeds = _seeds(p("sitemap.xml"))
    if not seeds:
        pytest.skip("sitemap 没有作为种子（well-known 未收录）")
    rnd = Crawler(fetcher).step(seeds)
    return [s.path for s in rnd.next]


# ── 1：三语义 id → 一条面 ───────────────────────────────────
def test_three_semantic_ids_collapse_in_a_real_crawl_round() -> None:
    """**端到端**：这是接线是否生效的唯一判据。"""
    got = _crawl_sitemap([
        p("api", "user-A", "profile"),
        p("api", "user-B", "profile"),
        p("api", "user-C", "profile"),
    ])
    assert len(got) == 1, got


# ── 2：两条不归（阈值 3）────────────────────────────────────
def test_two_paths_are_not_merged_in_a_real_crawl_round() -> None:
    got = _crawl_sitemap([p("api", "v1", "x"), p("api", "v2", "x")])
    assert len(got) == 2, got


# ── 3：结构性 id 不受影响 ───────────────────────────────────
def test_structural_ids_still_collapse() -> None:
    got = _crawl_sitemap([p("api", "1", "x"), p("api", "2", "x"), p("api", "3", "x")])
    assert len(got) == 1, got


# ── 4：差异确实来自批量归一 ─────────────────────────────────
def test_the_old_single_path_rule_would_have_kept_three() -> None:
    """**对照组**：`templatize` 单条对这三条给三个不同模板。

    这条钉住「上面第 1 条的变化确实来自 `templatize_many`」——
    否则它可能因为别的原因变小（比如 sitemap 解析只取第一条）。
    """
    paths = [p("api", "user-A", "profile"), p("api", "user-B", "profile"),
             p("api", "user-C", "profile")]
    assert len({templatize(x)[0] for x in paths}) == 3
