"""契约来源：把资产自述的机器可读契约变成候选（27 §3.2 / §3.8）。"""

from __future__ import annotations

from typing import Iterable

from ..types import (
    ContractHostError,
    RouteCandidate,
    RouteProvenance,
    RouteQuery,
)
from .base import ContractOp, SourceError, body_shape_sha256, matches_hints

_S = chr(0x2F)
_DS = chr(0x2F) * 2


class ContractSource:
    name = "contract"
    provenance = RouteProvenance.CONTRACT

    def __init__(self, ops: Iterable[ContractOp] = ()) -> None:
        self._ops = list(ops)

    def collect(self, q: RouteQuery) -> list[RouteCandidate]:
        out: list[RouteCandidate] = []
        for op in self._ops:
            candidate = RouteCandidate(
                method=op.method,
                template=op.template,
                param_names=op.param_names,
                auth_requirement=op.auth_requirement,
                body_shape_sha256=body_shape_sha256(op.body_schema),
                source=self.provenance,
                verifications=0,
                response_shape={"semantic_tags": list(op.semantic_tags)} if op.semantic_tags else {},
            )
            if matches_hints(candidate, q):
                out.append(candidate)
        return out


def validate_contract_hosts(servers: Iterable[str], asset_hosts: Iterable[str]) -> None:
    """契约里声明的 host 必须落在 assets 允许集合内，否则启动期拒绝（VP-1006）。

    只做集合比对；不解析、不归一成地址（L0-32）。
    """
    allowed = {h.lower() for h in asset_hosts}
    for server in servers:
        host = server.split(_DS)[-1].split(_S)[0].split(":")[0].lower()
        if host and host not in allowed:
            raise ContractHostError(f"契约 host {host} 不在 assets 允许集合内")


def parse_operations(document: object) -> list[ContractOp]:
    """机械判定“是不是契约”：能解析出 >=1 个 operation 才算（27 §3.8 终止铁律）。

    ``document`` 是由定位器取到的已解析对象；这里只认两种最简形状：
    已构建好的 ``ContractOp`` 列表，或含 ``operations`` 键的映射。
    解析不出 → 返回空列表，调用方降级下一级（L0-31）。
    """
    if isinstance(document, tuple) and all(isinstance(x, ContractOp) for x in document):
        return list(document)
    if isinstance(document, list) and all(isinstance(x, ContractOp) for x in document):
        return list(document)
    if isinstance(document, dict):
        ops = document.get("operations")
        if isinstance(ops, list):
            return [x for x in ops if isinstance(x, ContractOp)]
    raise SourceError("contract", "文档无法解析出 operation")
