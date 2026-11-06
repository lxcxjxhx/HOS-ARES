"""观测来源：既有流量证据（capsule / 夹具 / HAR）里真实发生过的请求（27 §3.2）。

base_confidence = 1.00：真实发生过的请求是最强信号。

同文件里还有 ``CrawlSource``：形态一模一样，只是**证据强度不同**——
爬取看到的是"页面上有这条链接"，不是"这条请求成功过"。两者共用一个实现，
靠 provenance 分流置信度（1.00 vs 0.70），账本里因此一眼分得开。
"""

from __future__ import annotations

from typing import Iterable

from ..types import RouteCandidate, RouteProvenance, RouteQuery
from .base import ObservedRecord, candidate_from_record, matches_hints


class ObservedSource:
    name = "observed"
    provenance = RouteProvenance.OBSERVED

    def __init__(self, records: Iterable[ObservedRecord] = ()) -> None:
        self._records = list(records)

    def collect(self, q: RouteQuery) -> list[RouteCandidate]:
        out: list[RouteCandidate] = []
        for rec in self._records:
            candidate = candidate_from_record(rec, source=self.provenance)
            if matches_hints(candidate, q):
                out.append(candidate)
        return out


class CrawlSource(ObservedSource):
    """爬取来源：候选来自页面上的链接/表单，**尚未请求过**。

    与 ``ObservedSource`` 的唯一差别是 ``provenance``——置信度由
    ``routes.confidence.BASE_CONFIDENCE`` 查表得出（crawl 0.70 < observed 1.00），
    「看见」与「打过」在报告里必须能分开，否则覆盖率会是假的。
    """

    name = "crawl"
    provenance = RouteProvenance.CRAWL
