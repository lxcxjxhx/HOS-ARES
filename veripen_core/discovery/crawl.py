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

import json
from dataclasses import dataclass
from typing import Any, Callable, Sequence
from urllib.parse import urlsplit

from ..routes.sources.base import ObservedRecord
from ..routes.types import RouteBinding, RouteCandidate, RouteProvenance
from .links import SEP, Sighting, extract, normalize_href, query_names
from .wellknown import WELL_KNOWN, paths_for
from .templatize import templatize

#: 单轮最多抓几页：一轮就是一份请求预算，不因为"还没爬完"就继续。
MAX_PAGES_PER_ROUND = 20

#: 单轮最多产几条新面：防止一个导航页把整轮记录挤满。
MAX_RECORDS_PER_ROUND = 200

#: 单个响应体最多解析这么多字节。10MB 的首页只值一份预算，不值一整轮。
BODY_LIMIT = 256 * 1024

#: ``fetch(binding) -> (status, content_type, body) | None``；拿不到（超时/闸门拒/异常）返回 None。
#: content_type 是**观测事实**：没有契约时，标签只能从它与 body 形状推（见 routes/tagging.py）。
Fetcher = Callable[[RouteBinding], "tuple[int, str, str] | None"]


def _json_fields(body: str, content_type: str) -> tuple[str, ...]:
    """从 JSON 响应里取顶层字段名，供"单对象读/列表读"的结构推断用。

    只看顶层：嵌套结构要靠契约（``$ref``/``allOf``），这里推不动也不要装作能推。
    """
    if "json" not in content_type.lower():
        return ()
    try:
        data = json.loads(body[:BODY_LIMIT])
    except (ValueError, TypeError):
        return ()
    if isinstance(data, dict):
        return tuple(str(key) for key in list(data)[:20])
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return tuple(str(key) for key in list(data[0])[:20])
    return ()


def _page_path(binding: RouteBinding) -> str:
    """当前页的路径。相对链接只有相对它才解析得对——不解析就会攒出 ``/../../x``。"""
    template = binding.template
    if "://" in template:
        return urlsplit(template).path or SEP
    return template or SEP


def transport_fetcher() -> Fetcher:
    """把 ``tools.http.send_body`` 包成 crawler 要的 fetch。**共用同一条闸门链**。

    读 HTML 也是一次出网；为拿 body 另开"只读不审"的近路，盲区正好落在发现阶段。
    """

    def _fetch(binding: RouteBinding) -> tuple[int, str, str] | None:
        from ..tools import http as http_tool

        try:
            trace, payload = http_tool.send_body(binding, {})
        except Exception:  # noqa: BLE001 - 闸门拒/超时/坏证书都按"这页没拿到"处理
            return None
        lowered = {str(k).lower(): str(v) for k, v in trace.headers.items()}
        return (
            trace.status,
            lowered.get("content-type", ""),
            payload.decode("utf-8", errors="replace"),
        )

    return _fetch


def well_known_candidates(base_symbol: str) -> list[RouteCandidate]:
    """robots.txt / sitemap.xml 作为**第一轮**候选去抓。

    它们本身不是"面"（抓到后只解析、不记录），但经常是静态站最全的目录来源。
    """
    return [
        RouteCandidate(
            method="GET",
            template="{" + base_symbol + "}" + path,
            source=RouteProvenance.CRAWL,
        )
        for path in WELL_KNOWN
    ]


def seed_candidate(base_symbol: str) -> RouteCandidate:
    """入口面：只有基址，没有路径——首页那一条。"""
    return RouteCandidate(
        method="GET",
        template="{" + base_symbol + "}",
        source=RouteProvenance.CRAWL,
    )


def candidate_of(sighting: Sighting, base_symbol: str) -> RouteCandidate:
    """看见的入口 → 候选模板。基址由符号在解析期补，这里只拼相对部分。

    **保持具体路径**：这一轮是要真的把它抓下来，而带 ``{p1}`` 的模板没有参数值，
    ``transport.render`` 会以"仍有未替换参数"直接拒绝——那条面就白白丢了。
    归一（``/api/users/1`` → ``/api/users/{p1}``）留到**记录**时做：
    抓到的形状属于这条面，而面是按模板去重的。
    """
    suffix = ("?" + sighting.query) if sighting.query else ""
    return RouteCandidate(
        method=sighting.method,
        template="{" + base_symbol + "}" + sighting.path + suffix,
        param_names=sighting.params,
        source=RouteProvenance.CRAWL,
    )


@dataclass(frozen=True)
class CrawlRound:
    """一轮的结果。``next`` 是**还没绑定的**入口，交给调用方去解析。"""

    fetched: int
    records: tuple[ObservedRecord, ...]
    next: tuple[Sighting, ...]
    skipped: tuple[tuple[str, str], ...]
    truncated: bool
    #: 本轮渲染了几页（0 = 没开渲染，或没有 HTML 面）
    rendered: int = 0

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
        renderer: Any = None,
        max_renders: int = 5,
    ) -> None:
        self._fetch = fetch
        self._max_pages = max_pages
        self._max_records = max_records
        #: 可选：BrowserRenderer。只补"静态 HTML 里看不见"的入口（JS 生成 / XHR）。
        self._renderer = renderer
        self._max_renders = max_renders
        self._renders = 0
        self._seen: set[str] = set()

    def step(self, seeds: Sequence[RouteBinding]) -> CrawlRound:
        records: list[ObservedRecord] = []
        pending: list[Sighting] = []
        skipped: list[tuple[str, str]] = []
        seen_templates: set[str] = set()
        fetched = 0
        render_total = 0
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
            status, content_type, body = result
            if status >= 400:
                skipped.append((key[:80], f"http_{status}"))
                continue

            # well-known 只解析、不记录：它们不是可判定的面，价值在里面的路径清单。
            page = _page_path(binding)
            if page in WELL_KNOWN:
                for found in paths_for(page, body[:BODY_LIMIT]):
                    parsed = normalize_href(found)
                    if parsed is None:
                        continue
                    raw = found.split("?", 1)
                    sighting = Sighting(
                        method="GET", path=raw[0],
                        params=query_names(raw[1]) if len(raw) > 1 else (),
                        query=raw[1] if len(raw) > 1 else "",
                    )
                    template, _p = templatize(sighting.path)
                    if template in seen_templates:
                        continue
                    seen_templates.add(template)
                    pending.append(sighting)
                continue

            # 记录属于**这条被真正抓过的面**，不属于页面里看见的链接。
            # 早期版本把父页的 content-type 贴在子链接上，于是 JSON 接口被当成
            # text/html，object_read 永远推不出来——形状必须是"自己的响应"。
            if len(records) >= self._max_records:
                truncated = True
                break
            # 具体路径 → 模板：面按模板去重，抓到的形状属于这条面。
            # **查询串要单独取**：它不在路径段里，只 templatize 路径会把它整段吞掉，
            # 于是 param_injectable 永远推不出来（带 ?q= 的面被当成无参静态页）。
            raw_identity = binding.identity_template or _page_path(binding)
            identity_path, _, query = raw_identity.partition("?")
            identity, path_params = templatize(identity_path)
            param_names = path_params + query_names(query)
            if identity in seen_templates:
                continue
            seen_templates.add(identity)
            records.append(
                ObservedRecord(
                    method=binding.method,
                    template=identity,
                    param_names=param_names,
                    content_type=content_type,
                    fields=_json_fields(body, content_type),
                    verifications=1,  # 真的抓到了：这次请求发生过
                )
            )

            # 渲染补充：SPA 的入口和接口调用都在 JS 里，源码看不到。
            rendered = 0
            if (
                self._renderer is not None
                and "text/html" in content_type.lower()
                and self._renders < self._max_renders
            ):
                self._renders += 1
                rendered = 1
                capture = self._renderer.capture(url=binding.template)
                if capture.outcome == "ok":
                    for extra in extract(capture.html[:BODY_LIMIT],
                                         base_path=_page_path(binding)):
                        template, _p = templatize(extra.path)
                        if template in seen_templates:
                            continue
                        seen_templates.add(template)
                        pending.append(extra)
                    for path in capture.paths:
                        parsed = normalize_href(path)
                        if parsed is None:
                            continue
                        raw = path.split("?", 1)
                        sighting = Sighting(
                            method="GET", path=raw[0],
                            params=query_names(raw[1]) if len(raw) > 1 else (),
                            query=raw[1] if len(raw) > 1 else "",
                        )
                        template, _p = templatize(sighting.path)
                        if template in seen_templates:
                            continue
                        seen_templates.add(template)
                        pending.append(sighting)
                skipped.append((key[:80], f"render_{capture.outcome}"))
            render_total += rendered

            for sighting in extract(body[:BODY_LIMIT], base_path=_page_path(binding)):
                template, _params = templatize(sighting.path)
                if template in seen_templates:
                    continue
                seen_templates.add(template)
                pending.append(sighting)

        return CrawlRound(
            fetched=fetched,
            records=tuple(records),
            next=tuple(pending),
            skipped=tuple(skipped),
            truncated=truncated,
            rendered=render_total,
        )


__all__ = [
    "BODY_LIMIT",
    "MAX_PAGES_PER_ROUND",
    "MAX_RECORDS_PER_ROUND",
    "CrawlRound",
    "Crawler",
    "Fetcher",
    "candidate_of",
    "seed_candidate",
    "transport_fetcher",
]
