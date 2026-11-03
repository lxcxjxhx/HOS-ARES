"""oracle 需要的「面」：**从 oracle 类本身派生**，不在脚本里各留一份字典。

为什么必须收进来（此前它散在四处，其中一处已经分叉）：

``EXTRA_TAGS`` 决定「这个 oracle 除了主绑定之外还要不要第二条面」。它对判定的
影响是直接的：``bola_functional`` 少了 ``normal_function``，``run_oracle`` 会抛
``SymbolMissing`` → BLOCKED。所以**这不是配置，是判定输入的一部分**——
四个脚本各维护一份，分叉时不会红，只会静默改变一条用例的结论。

实测已经发生过：``scripts/demo.py`` 的副本缺 ``xss_stored_probe``。
demo 恰好不跑那三类所以没暴露，但那是运气。

**: 为什么派生而不是手写常量**：手写那份我写过一次，立刻错了两处——
写了个不存在的 ``idor_enumerable``，又把 ``cors_misconfig`` 的主 tag 记成
``api_read``（其实是 ``api_read`` 但 ``xss_reflected_ctx`` 才是 ``echo_param``，
两条容易记串）。``BaseOracle.meta.selector_tags`` 本来就是权威：
第一个是主面，其余是附加面。手写常量只是把同一个事实抄错的机会。
"""

from __future__ import annotations

from veripen_oracles import ALL_ORACLES


def _tags_by_oracle() -> dict[str, tuple[str, ...]]:
    out: dict[str, tuple[str, ...]] = {}
    for cls in ALL_ORACLES:
        tags = tuple(cls.meta.selector_tags)
        if tags:
            out[cls.meta.id] = tags
    return out


#: oracle_id → 全部 selector（首个为主面，其余为附加面）。构建期一次，之后只读。
TAGS_BY_ORACLE: dict[str, tuple[str, ...]] = _tags_by_oracle()


def primary_tag(oracle_id: str) -> str:
    """主面。缺失时抛 KeyError——静默返回空串会让 oracle 拿到错误的绑定。"""
    return TAGS_BY_ORACLE[oracle_id][0]


def extra_tags(oracle_id: str) -> tuple[str, ...]:
    """附加面；无则空元组。**唯一入口**，不要在调用点另建字典。"""
    tags = TAGS_BY_ORACLE.get(oracle_id, ())
    return tags[1:]


__all__ = ["TAGS_BY_ORACLE", "extra_tags", "primary_tag"]
