"""oracle 包（B5 起）：TierA 4 类 + OOB 通道 + 骨架。

纪律（07 §6 三步验收）：
- 判定正确：``pytest tests/unit -k oracle`` 正全 VERIFIED、负全 REFUTED/INCONCLUSIVE
- 不碰内核：``git diff --stat veripen_core/`` 为空
- 零地址：``make lint-noroute`` 0 命中（模板字面量只允许在 ``tests/lab``）
"""

from __future__ import annotations

from .common.base import BaseOracle, OracleMeta
from .common.harness import (
    Nonce,
    Pair,
    SymbolMissing,
    SymbolTable,
    Trace,
    Transform,
    Variant,
    pair_up,
    run,
    three_way_holds,
)

#: 全部已注册 oracle（B5 的 4 类 + B6 的 2 类）；B7 起继续追加
from . import tierA, tierA_rest  # noqa: E402

TIER_A: tuple[type[BaseOracle], ...] = tierA.REGISTRY + (
    tierA_rest.OpenRedirect,
    tierA_rest.PathTraversal,
)

__all__ = [
    "BaseOracle",
    "Nonce",
    "OracleMeta",
    "Pair",
    "SymbolMissing",
    "SymbolTable",
    "Trace",
    "Transform",
    "Variant",
    "pair_up",
    "run",
    "three_way_holds",
    "TIER_A",
    "tierA",
    "tierA_rest",
]
