"""B86 · 一轮爬取：抓给定绑定 → 抽入口 → 产出可进 routes 的观测记录。

**轮次模型**（这是与"爬虫引擎"的关键差别）：

我们不在 crawler 里跟链接。因为"跟"意味着发请求，而发请求必须持
``RouteBinding``（27 N1：地址只能由 resolver 出）。所以一次 ``step`` 只做三件事：

    抓这一轮已经绑定的面 → 从响应体抽入口 → 把**相对模板**交出去

下一轮由调用方把这些相对模板喂回 resolver 拿到新绑定，再进来抓。
这样"地址纪律"在爬取路径上不被破例，爬多深也只是多几轮，不是多一条旁路。

对应 strix 的位置：``strix/tools/proxy/tools.py`` 的 sitemap 观察——
差别只在观察点（它看代理流量，我们看响应体），不在结论形态（都是候选面清单）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence
from urllib.parse import urlsplit

from ..routes.sources.base import ObservedRecord
from ..routes.types import RouteBinding
from .links import SEP, Sighting, extract
from .templatize import templatize

#: 单轮最多抓几页：一轮就是一份请求预算，不因为"还没爬完"就继续。
MAX_PAGES_PER_ROUND = 20

#: 单轮最多产几条新面：防止一个导航页把整轮记录挤满。
MAX_RECORDS_PER_ROUND = 200

#: 单个响应体最多解析这么多字节。10MB 的首页只值一份预算，不值一整轮。
BODY_LIMIT = 256 * 1024

#: ``fetch(binding) -> (status, body) | None``；拿不到（超时/闸门拒/异常）返回 None。
Fetcher = Callable[[RouteBinding], "tuple[int, str] | None"]


def _page_path(binding: RouteBinding) -> str:
    """当前页的路径。相对链接只有相对它才解析得对——不解析就会攒出 ``/../../x``。"""
    template = binding.template
    if "://" in template:
        return urlsplit(template).path or SEP
    return template or SEP


@dataclass(frozen=True)
class CrawlRound:
    """一轮的结果。``next`` 是**还没绑定的**入口，交给调用方去解析。"""

    fetched: int
    records: tuple[ObservedRecord, ...]
    next: tuple[Sighting, ...]
    skipped: tuple[tuple[str, str], ...]
    truncated: bool

    @property
    def counts(self) -> dict[str, int]:
        return {
            "fetched": self.fetched,
            "records": len(self.records),
            "next": len(self.next),
            "skipped": len(self.skipped),
        }


class Crawler:
    """跨轮存活：``_seen`` 保证同一个面不会被重复抓（否则第二轮又抓首页）。"""

    def __init__(
        self,
        fetch: Fetcher,
        *,
        max_pages: int = MAX_PAGES_PER_ROUND,
        max_records: int = MAX_RECORDS_PER_ROUND,
    ) -> None:
        self._fetch = fetch
        self._max_pages = max_pages
        self._max_records = max_records
        self._seen: set[str] = set()

    def step(self, seeds: Sequence[RouteBinding]) -> CrawlRound:
        records: list[ObservedRecord] = []
        pending: list[Sighting] = []
        skipped: list[tuple[str, str]] = []
        seen_templates: set[str] = set()
        fetched = 0
        truncated = False

        for binding in seeds:
            if fetched >= self._max_pages:
                truncated = True
                break
            # 身份用 identity_template（不含部署地址），换台机器同一面仍是同一面。
            key = binding.identity_template or binding.template
            if key in self._seen:
                continue
            self._seen.add(key)

            result = self._fetch(binding)
            fetched += 1
            if result is None:
                skipped.append((key[:80], "fetch_failed"))
                continue
            status, body = result
            if status >= 400:
                skipped.append((key[:80], f"http_{status}"))
                continue

            for sighting in extract(body[:BODY_LIMIT], base_path=_page_path(binding)):
                template, params = templatize(sighting.path)
                if template in seen_templates:
                    continue
                if len(records) >= self._max_records:
                    truncated = True
                    break
                seen_templates.add(template)
                records.append(
                    ObservedRecord(
                        method=sighting.method,
                        template=template,
                        param_names=params + sighting.params,
                        # 0 = "看见过这条面，还没请求过它"。真的打过去之后
                        # 由下一次 transport 观测把它升成 1，置信度自己会涨。
                        verifications=0,
                    )
                )
                pending.append(sighting)

        return CrawlRound(
            fetched=fetched,
            records=tuple(records),
            next=tuple(pending),
            skipped=tuple(skipped),
            truncated=truncated,
        )


__all__ = [
    "BODY_LIMIT",
    "MAX_PAGES_PER_ROUND",
    "MAX_RECORDS_PER_ROUND",
    "CrawlRound",
    "Crawler",
    "Fetcher",
]
