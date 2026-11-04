"""B57 跨任务记忆：资产事实可复用，**结论不可复用**。

三条反滥用规则（`H04 §4.1`），每条都有机械判据：

1. **结论不跨任务**：记忆层只写"资产事实"，API 里没有任何返回 verdict 的路径；
   删掉全部记忆，同一份判定输入的结果不变（测试直接比对）。
2. **记忆必须有出处**：写入要求 `source_event_id`，没有就拒（复用 I10 的 evidence 约束）。
3. **过期即失效**：超过 TTL 的事实不进 `recall()`，进 `stale()`，**不删除**（I10）。

实现复用 `blackboard_facts`：predicate 前缀 `mem.`，origin=`observed`，
所以"记忆"与"本轮观测"在同一条时间线上，审计能一起看。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .blackboard import Blackboard, Fact

#: 记忆谓词前缀（与本轮观测区分，便于过滤与审计）
MEMORY_PREFIX = "mem."

#: 默认 TTL：30 天（H04 §4.1）
DEFAULT_TTL_S = 30 * 24 * 3600


class MemoryError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class MemoryEntry:
    asset: str
    kind: str
    value: str
    source_event_id: str
    created_at: int
    ttl_s: int

    def expired(self, now: int) -> bool:
        return now >= self.created_at + self.ttl_s


class Memory:
    """跨任务资产事实。**只写事实，不写判定结论**。"""

    def __init__(self, blackboard: Blackboard) -> None:
        self.blackboard = blackboard

    # ── 写 ─────────────────────────────────────────────────────
    def remember(self, *, asset: str, kind: str, value: str,
                 source_event_id: str, task_id: str = "memory",
                 ttl_s: int = DEFAULT_TTL_S) -> Fact:
        if not source_event_id.strip():
            raise MemoryError("missing_source", "记忆必须有 source_event_id（无出处不写入）")
        if not asset.strip() or not kind.strip():
            raise MemoryError("bad_key", "asset 与 kind 都不能为空")
        return self.blackboard.append(
            task_id=task_id,
            subject=asset,
            predicate=f"{MEMORY_PREFIX}{kind}",
            value=value,
            origin="observed",
            evidence_id=source_event_id,
            from_target=False,
            ttl_s=ttl_s,
        )

    # ── 读 ─────────────────────────────────────────────────────
    def _entries(self, asset: str | None) -> list[MemoryEntry]:
        facts: Sequence[Fact] = self.blackboard.all("memory")
        out: list[MemoryEntry] = []
        for fact in facts:
            if not fact.predicate.startswith(MEMORY_PREFIX):
                continue
            if asset is not None and fact.subject != asset:
                continue
            out.append(MemoryEntry(
                asset=fact.subject,
                kind=fact.predicate[len(MEMORY_PREFIX):],
                value=fact.value,
                source_event_id=fact.evidence_id,
                created_at=fact.created_at,
                ttl_s=fact.ttl_s or DEFAULT_TTL_S,
            ))
        out.sort(key=lambda e: (e.asset, e.kind, -e.created_at))
        return out

    def recall(self, asset: str | None = None, *, now: int) -> tuple[MemoryEntry, ...]:
        """注入视图：**过期的不给**（不是删掉，是"这次不注入"）。"""
        return tuple(e for e in self._entries(asset) if not e.expired(now))

    def stale(self, asset: str | None = None, *, now: int) -> tuple[MemoryEntry, ...]:
        """过期集合：审计要看"我们曾经以为是什么"，所以留着。"""
        return tuple(e for e in self._entries(asset) if e.expired(now))

    def as_prompt_lines(self, asset: str, *, now: int) -> tuple[str, ...]:
        """注入 L1a 的形态：**一行一条事实 + 出处**，不含任何结论语气。"""
        return tuple(f"{e.asset}.{e.kind} = {e.value}（出处 {e.source_event_id}）"
                     for e in self.recall(asset, now=now))


__all__ = ["DEFAULT_TTL_S", "MEMORY_PREFIX", "Memory", "MemoryEntry", "MemoryError"]
