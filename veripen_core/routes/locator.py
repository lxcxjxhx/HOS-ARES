"""契约位置自举（27 §3.8）：三级定位、最多 3 跳、防环、机械判定。

铁律：
- 每一跳的 ``ref`` 必须互不相同，重复即 ``CONTRACT_LOCATOR_UNRESOLVED`` 并记录环。
- 总跳数 > 3 → 停止。
- 取到的文档必须能解析出 >=1 个 operation，否则降级下一级（不把 404 页当契约）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .types import ContractLocatorUnresolved

MAX_HOPS = 3


@dataclass
class LocatorLevel:
    """一级定位手段。``resolve`` 返回 (ref, document) 或 None。

    ref 是这一跳的身份（不透明），document 是已解析对象。
    """

    name: str
    resolve: Callable[[], tuple[str, object] | None]


@dataclass
class LocatorResult:
    document: object
    ref: str
    hops: int
    visited: list[str] = field(default_factory=list)


def locate_contract(
    levels: list[LocatorLevel],
    parse_ok: Callable[[object], bool],
    max_hops: int = MAX_HOPS,
) -> LocatorResult:
    visited: list[str] = []
    hops = 0
    for level in levels:
        if hops >= max_hops:
            raise ContractLocatorUnresolved(
                f"超过最大跳数 {max_hops}；已访问 {visited}"
            )
        got = level.resolve()
        if got is None:
            continue
        ref, document = got
        hops += 1
        if ref in visited:
            raise ContractLocatorUnresolved(f"自举成环：{ref} 重复出现；环={visited + [ref]}")
        visited.append(ref)
        if not parse_ok(document):
            # 解析不出 operation：视为未找到，降级下一级（L0-31）
            continue
        return LocatorResult(document=document, ref=ref, hops=hops, visited=visited)
    raise ContractLocatorUnresolved(f"三级定位均未取到可解析契约；已访问 {visited}")
