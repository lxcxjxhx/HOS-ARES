"""观测来源：既有流量证据（capsule / 夹具 / HAR）里真实发生过的请求（27 §3.2）。

base_confidence = 1.00：真实发生过的请求是最强信号。
"""

from __future__ import annotations

from typing import Iterable

from ..types import RouteCandidate, RouteProvenance, RouteQuery
from .base import ObservedRecord, body_shape_sha256, matches_hints


class ObservedSource:
    name = "observed"
    provenance = RouteProvenance.OBSERVED

    def __init__(self, records: Iterable[ObservedRecord] = ()) -> None:
        self._records = list(records)

    def collect(self, q: RouteQuery) -> list[RouteCandidate]:
        out: list[RouteCandidate] = []
        for rec in self._records:
            candidate = RouteCandidate(
                method=rec.method,
                template=rec.template,
                param_names=rec.param_names,
                auth_requirement=rec.auth_requirement,
                body_shape_sha256=body_shape_sha256(rec.body_schema),
                source=self.provenance,
                verifications=rec.verifications,
            )
            if matches_hints(candidate, q):
                out.append(candidate)
        return out
