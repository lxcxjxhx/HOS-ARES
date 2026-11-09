"""B52 上下文压缩：只压上下文，不压证据、不压判定。

三种算法（`H03 §4`）：

1. **事实摘要** —— 同 `(subject, predicate)` 多条事实合成一行，带 `merged_from`（可回溯到原 id）；
2. **步骤摘要** —— 最近 N 步事件压成 ≤5 行（动作/结果/影响），更早的只报计数；
3. **证据按需** —— 上下文里只放 capsule id，正文要显式 `expand()` 才取。

**红线**：`verdicts` 原样返回、指纹不变（`17 I1/I2`）；压缩关闭时回落"裁剪"（丢最旧），
同样不改判定——这正是"压缩只是上下文的搬运工"的可执行形式。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from .context import verdict_fingerprint

class FactLike(Protocol):
    """压缩只读四件事：``id`` / ``subject`` / ``predicate`` / ``value``。

    用 Protocol 而不是绑定具体类：``Fact``（本次任务）与 ``AssetFact``（跨任务记忆）
    字段不同、语义不同，但压缩对它们做的事完全一样。硬绑一个类只会逼调用方
    手写一遍映射——那段映射除了多一次出错机会，什么也不产生。
    """

    @property
    def id(self) -> str: ...

    @property
    def subject(self) -> str: ...

    @property
    def predicate(self) -> str: ...

    @property
    def value(self) -> str: ...


#: 估算口径：4 字符 ≈ 1 token（与 budget 的粗算一致，不引入分词器依赖）
CHARS_PER_TOKEN = 4

#: 步骤摘要最多几行
STEP_LINES = 5

NL = chr(10)


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


@dataclass(frozen=True)
class FactLine:
    subject: str
    predicate: str
    values: tuple[str, ...]
    merged_from: tuple[str, ...]

    def render(self) -> str:
        values = "|".join(self.values)
        return f"{self.subject}.{self.predicate} = {values} (n={len(self.merged_from)})"


@dataclass(frozen=True)
class Compressed:
    facts: tuple[FactLine, ...]
    steps: tuple[str, ...]
    evidence: tuple[str, ...]
    text: str
    tokens_before: int
    tokens_after: int
    verdicts_fingerprint: str
    mode: str = "summarized"          # summarized | truncated（回落）
    dropped: Mapping[str, int] = field(default_factory=dict)

    @property
    def reduction(self) -> float:
        if self.tokens_before == 0:
            return 0.0
        return 1.0 - (self.tokens_after / self.tokens_before)


def summarize_facts(facts: Sequence[FactLike]) -> tuple[FactLine, ...]:
    """按 `(subject, predicate)` 合并；值去重保序，`merged_from` 记录全部原 id。"""
    groups: dict[tuple[str, str], list[FactLike]] = {}
    for fact in facts:
        groups.setdefault((fact.subject, fact.predicate), []).append(fact)
    lines: list[FactLine] = []
    for (subject, predicate), members in sorted(groups.items()):
        values: list[str] = []
        for member in members:
            if member.value not in values:
                values.append(member.value)
        lines.append(FactLine(subject=subject, predicate=predicate,
                              values=tuple(values),
                              merged_from=tuple(f.id for f in members)))
    return tuple(lines)


def summarize_steps(events: Sequence[Mapping[str, Any]],
                    *, limit: int = STEP_LINES) -> tuple[str, ...]:
    """最近 `limit` 步一行一条；更早的只报计数（不假装看过）。"""
    tail = list(events)[-limit:]
    lines = [
        f"{event.get('kind', '?')}:{event.get('tool') or event.get('oracle_id') or '-'}:"
        f"{'ok' if event.get('ok') else 'fail'}"
        for event in tail
    ]
    older = len(events) - len(tail)
    if older > 0:
        lines.append(f"... 更早 {older} 步未展开")
    return tuple(lines)


def _raw_text(facts: Sequence[FactLike], events: Sequence[Mapping[str, Any]]) -> str:
    fact_part = [f"{f.subject}.{f.predicate}={f.value}" for f in facts]
    event_part = [
        f"{e.get('kind')}:{e.get('tool') or e.get('oracle_id') or '-'}:"
        f"{'ok' if e.get('ok') else 'fail'}"
        for e in events
    ]
    return NL.join(fact_part + event_part)


def compress(
    *,
    facts: Sequence[FactLike],
    events: Sequence[Mapping[str, Any]],
    capsule_ids: Iterable[str],
    verdicts: Mapping[str, str],
    summarize: bool = True,
    limit_steps: int = STEP_LINES,
) -> Compressed:
    """一次压缩。``summarize=False`` 是回落路径：只裁剪，不合并。"""
    before_text = _raw_text(facts, events)
    before = estimate_tokens(before_text) if before_text.strip() else 0

    if summarize:
        fact_lines = summarize_facts(facts)
        step_lines = summarize_steps(events, limit=limit_steps)
        mode = "summarized"
        dropped: dict[str, int] = {}
    else:
        keep = list(facts)[-10:]
        fact_lines = summarize_facts(keep)
        step_lines = summarize_steps(events, limit=limit_steps)
        mode = "truncated"
        dropped = {"facts": max(0, len(facts) - len(keep)),
                   "steps": max(0, len(events) - limit_steps)}

    capsule_list = tuple(dict.fromkeys(str(cid) for cid in capsule_ids))
    parts = [
        NL.join(line.render() for line in fact_lines),
        NL.join(step_lines),
        ("ev:" + ",".join(capsule_list)) if capsule_list else "ev:-",
    ]
    text = NL.join(part for part in parts if part)
    after = estimate_tokens(text) if text.strip() else 0

    if summarize and before and after >= before:
        # 压不动就别压（B177）：小输入摊成带表头的摘要只会更贵。
        # 原样返回并如实标 passthrough——"压缩"交出比输入更大的东西，不是压缩。
        return Compressed(
            facts=fact_lines,
            steps=step_lines,
            evidence=capsule_list,
            text=before_text,
            tokens_before=before,
            tokens_after=before,
            verdicts_fingerprint=verdict_fingerprint(verdicts),
            mode="passthrough",
            dropped={},
        )

    return Compressed(
        facts=fact_lines,
        steps=step_lines,
        evidence=capsule_list,
        text=text,
        tokens_before=before,
        tokens_after=after,
        verdicts_fingerprint=verdict_fingerprint(verdicts),
        mode=mode,
        dropped=dropped,
    )


def expand(capsule_ids: Sequence[str], *, wanted: Iterable[str],
           loader: Callable[[str], str]) -> dict[str, str]:
    """证据按需：**只加载明确要的那几个**，其余留在 id 形态。"""
    wanted_set = set(wanted)
    return {cid: loader(cid) for cid in capsule_ids if cid in wanted_set}


__all__ = [
    "CHARS_PER_TOKEN",
    "Compressed",
    "FactLine",
    "STEP_LINES",
    "compress",
    "estimate_tokens",
    "expand",
    "FactLike",
    "summarize_facts",
    "summarize_steps",
]
