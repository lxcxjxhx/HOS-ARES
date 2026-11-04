"""B62 · L1a 跨任务记忆注入（08 §4 / `H11 B62`）。

这一层解决的是"上次测过的东西这次还用不用得上"，不是"这次该怎么判"。
三条纪律写死在代码里：

1. **只来自 SEALED capsule**：没有 ``capsule_id`` 的条目一律不注入（I11）。
   否则模型的一句猜测会被沉淀成"这家客户的长期事实"，下次运行当既定前提。
2. **≤10 条，且整段带前缀**：它只是"上次的"，不是本次观测。
3. **过期标 stale 不删**：过期数量本身就是信号，删掉等于销毁证据。

压缩交给 :func:`veripen_core.compression.compress`——它负责把同类事实合并、
给出 ``tokens_before/after``。降幅不足 30% 时本模块**不改判定**，只如实记录，
由调用方决定要不要调 ``summarize=False`` 的回落路径。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .blackboard import AssetFact, AssetFacts
from .compression import Compressed, compress, estimate_tokens
from .context import (
    KNOWLEDGE_SEGMENT,
    MAX_PER_STEP,
    Context,
    KnowledgeDoc,
    assert_no_leak,
    load_dir,
    select,
)

#: 08 §4 硬要求：注入前缀，读的人必须知道这是"上次结论"
L1A_PREFIX = "[上次结论，需本次复核]"

#: 单步注入条数上限（08 §4：<=10 条）
L1A_MAX = 10

#: 压缩前先取这么多条候选。**必须先全量压、再截行**：
#: 压 10 条小样本时合并同类项反而变长（每条从 `k=v` 变成 `k=v1|v2 (n=2)`），
#: 降幅会变成负数——那不是算法失效，是采样点选错了。
L1A_CANDIDATE_MAX = 30


@dataclass(frozen=True)
class L1a:
    """一次 L1a 组装的产物。``text`` 为空表示本次没有可注入的跨任务记忆。"""

    text: str
    used: tuple[AssetFact, ...] = ()
    stale_ids: tuple[str, ...] = ()
    dropped: int = 0
    compressed: Compressed | None = None
    #: 压缩前（候选全量）与截断后的 token 估计；降幅由这两者算，不由 Compressed 算
    tokens_before: int = 0
    tokens_after: int = 0

    @property
    def reduction(self) -> float:
        """压缩降幅（0–1）。候选为空时为 0。"""
        if not self.tokens_before:
            return 0.0
        return 1.0 - (self.tokens_after / self.tokens_before)

    def with_goal(self, goal: str) -> str:
        """把 L1a 段拼到目标串后面；空段时返回原 goal，行为与未接线等价。"""
        return f"{goal}\n{self.text}" if self.text else goal


def build_l1a(
    asset_facts: AssetFacts,
    asset_id: str,
    *,
    events: Sequence[Mapping[str, Any]] = (),
    verdicts: Mapping[str, str] | None = None,
    limit: int = L1A_MAX,
) -> L1a:
    """组装 L1a 段。

    ``asset_id`` 为空直接返回空段——没有资产标识就不该有"跨任务记忆"。
    """
    if not asset_id:
        return L1a(text="")

    # injectable 已做 live 过滤 + from_target 排序；这里取足候选，压缩后再截行
    candidates = asset_facts.injectable(asset_id, limit=L1A_CANDIDATE_MAX)
    usable = [f for f in candidates if f.capsule_id]
    dropped = len(candidates) - len(usable)

    live_ids = {f.id for f in asset_facts.live(asset_id)}
    stale_ids = tuple(f.id for f in asset_facts.all(asset_id) if f.id not in live_ids)

    if not usable:
        return L1a(text="", used=(), stale_ids=stale_ids, dropped=dropped, compressed=None)

    pressed = compress(
        facts=usable,
        events=events,
        capsule_ids=[f.capsule_id for f in usable],
        verdicts=verdicts or {},
    )
    kept = pressed.facts[:limit]
    dropped += max(0, len(pressed.facts) - len(kept))

    body_parts = ["\n".join(line.render() for line in kept)]
    if pressed.evidence:
        body_parts.append("ev:" + ",".join(pressed.evidence))
    body = "\n".join(part for part in body_parts if part)

    parts = [L1A_PREFIX, body]
    if stale_ids:
        parts.append(f"[另有 {len(stale_ids)} 条已过期，未注入]")
    return L1a(
        text="\n".join(parts),
        used=tuple(usable[:limit]),
        stale_ids=stale_ids,
        dropped=dropped,
        compressed=pressed,
        tokens_before=pressed.tokens_before,
        tokens_after=estimate_tokens(body),
    )


__all__ = ["L1A_MAX", "L1A_PREFIX", "L1a", "build_knowledge", "build_l1a"]


def build_knowledge(
    *,
    docs: Sequence[KnowledgeDoc] = (),
    tags: Sequence[str] = (),
    limit: int = MAX_PER_STEP,
    base_segments: Mapping[str, str] | None = None,
) -> Context:
    """把 knowledge 装进 L2 段（`H11 B63`）。

    **判定侧拿不到它**：``Context.judge_input()`` 只读 ``judge``，而这里的
    ``Context`` 从不设置 judge——想污染判定得先改 ``knowledge_free`` 的签名，
    那是 review 时一眼可见的改动。

    选中的条目数被 ``select`` 卡在 ``MAX_PER_STEP``（2 条/步），单条超
    ``MAX_BYTES`` 由加载端截断并标记。返回前跑一次 ``assert_no_leak``：
    正文若出现在任何非 L2 段，这里就炸，而不是等判定阶段才发现。
    """
    picked = select(list(docs), tags=tags, limit=limit)
    ctx = Context(segments=dict(base_segments or {}), judge="")
    if not picked:
        return ctx
    injected = ctx.with_knowledge(picked)
    assert_no_leak(injected, picked)
    return injected


def knowledge_text(ctx: Context) -> str:
    """L2 段正文（空串表示没有注入）。调用方据此决定要不要拼进 prompt。"""
    return ctx.segments.get(KNOWLEDGE_SEGMENT, "")
