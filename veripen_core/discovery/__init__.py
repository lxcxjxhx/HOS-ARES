"""B86 · 无契约发现（`H27`）：目标没有 OpenAPI 时，面从哪来。

三件事，三层各一件，互不越界：

| 层 | 模块 | 只做什么 |
|---|---|---|
| 抽取 | ``links`` | HTML 文本 → 相对入口（纯函数，不联网） |
| 归一 | ``templatize`` | 具体路径 → 模板 + 参数名（纯函数） |
| 轮次 | ``crawl`` | 已绑定的面 → 观测记录 + 下一轮入口（发请求由注入的 fetcher 做） |

**不在这里做的事**：构造 ``RouteBinding``（resolver 的唯一职责，N2）、
发请求（``tools.http.send`` + guard 的唯一职责）、挑 oracle（profile 的职责）。
"""

from __future__ import annotations

from .crawl import (
    BODY_LIMIT,
    MAX_PAGES_PER_ROUND,
    MAX_RECORDS_PER_ROUND,
    CrawlRound,
    Crawler,
    Fetcher,
    candidate_of,
    seed_candidate,
    transport_fetcher,
)
from .links import MAX_PER_PAGE, SEP, Sighting, extract, normalize_href
from .templatize import templatize


__all__ = [
    "BODY_LIMIT",
    "MAX_PAGES_PER_ROUND",
    "MAX_PER_PAGE",
    "MAX_RECORDS_PER_ROUND",
    "SEP",
    "CrawlRound",
    "Crawler",
    "Fetcher",
    "candidate_of",
    "seed_candidate",
    "transport_fetcher",
    "Sighting",
    "extract",
    "normalize_href",
    "templatize",
]
