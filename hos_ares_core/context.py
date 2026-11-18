"""B12.4 · knowledge（skill）层：**只进 L2，永不进判定**（36 §4.1、08 §4）。

这一层的价值不是"多喂点知识"，而是把**唯一一条安全的能力扩展**做实：
装 100 个 knowledge 是安全的，因为它碰不到 verdict。

三条红线，每条都有可执行的检查：
1. 知识正文**只能拼进 L2 段**，L1/L0/判定段一律拿不到（``Context`` 按段隔离）；
2. 改 knowledge 全文，**verdict 逐字不变**（``knowledge_free`` 纯函数 + 回归指纹）；
3. 加载上限硬约束：``<=2 条/步``、``<=1KB/条``，超了**截断并记账**，不静默丢。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

#: 36 §4.2 knowledge 准入的硬上限（不是建议值）
MAX_PER_STEP = 2
MAX_BYTES = 1024

#: 知识在上下文里的段名。**只有这一段允许出现 knowledge 正文。**
KNOWLEDGE_SEGMENT = "L2.knowledge"

#: 除 knowledge 段以外，任何段里出现知识正文都算泄漏。
#: 用纯量而非元组：段名清单不是数据表，是五种"信封"的名字。
FORBIDDEN_SEGMENTS: frozenset[str] = frozenset(
    {"L0", "L1", "L1a", "judge", "prompt"}
)


class KnowledgeError(RuntimeError):
    """知识文件不合规：超限、越出根目录、或试图进入判定段。**fail-closed**。"""

    code = "VP-8004"


@dataclass(frozen=True)
class KnowledgeDoc:
    id: str
    path: str
    body: str
    sha256: str
    truncated: bool = False

    @property
    def bytes_used(self) -> int:
        return len(self.body.encode("utf-8"))


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_dir(root: str | Path, *, max_bytes: int = MAX_BYTES) -> list[KnowledgeDoc]:
    """加载 ``knowledge/*.md``。**按 id 排序**：同输入同输出，不给"顺序随机"留口子。"""
    base = Path(root)
    if not base.exists():
        return []
    docs: list[KnowledgeDoc] = []
    for p in sorted(base.glob("*.md")):
        raw = p.read_text(encoding="utf-8")
        encoded = raw.encode("utf-8")
        truncated = len(encoded) > max_bytes
        # 截断按字节，但必须落在字符边界：否则会切出半个 UTF-8 字符
        body = encoded[:max_bytes].decode("utf-8", errors="ignore") if truncated else raw
        docs.append(
            KnowledgeDoc(id=p.stem, path=p.as_posix(), body=body,
                         sha256=_digest(raw), truncated=truncated)
        )
    return docs


def select(
    docs: Sequence[KnowledgeDoc], *, tags: Iterable[str] = (), limit: int = MAX_PER_STEP
) -> list[KnowledgeDoc]:
    """按标签挑**最多 2 条**。

    选中规则刻意简单：``id`` 命中任一标签优先，其余按 id 排序补足。
    没有"相关度打分"——打分器本身会成为判定路径上的一个变量。
    """
    wanted = {t.strip().lower() for t in tags if t and t.strip()}
    hits = [d for d in docs if d.id.lower() in wanted]
    rest = [d for d in docs if d.id.lower() not in wanted]
    return (hits + rest)[: max(0, limit)]


def render(docs: Sequence[KnowledgeDoc]) -> str:
    """拼成 L2 段正文。**带来源 id 与截断标记**：读的人要知道这只是一段参考。"""
    if not docs:
        return ""
    lines = ["[知识参考，非事实，不参与判定]"]
    for d in docs:
        mark = "（已截断）" if d.truncated else ""
        lines.append(f"- {d.id}{mark}: {d.body.strip()}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# 段隔离：知识进 L2，不许进判定
# ══════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class Context:
    """一次注入的上下文分段。``judge`` 段是判定输入，**只由 pairs 决定**。"""

    segments: Mapping[str, str]
    judge: str = ""

    def with_knowledge(self, docs: Sequence[KnowledgeDoc]) -> "Context":
        body = render(docs)
        if not body:
            return self
        merged = dict(self.segments)
        merged[KNOWLEDGE_SEGMENT] = body
        return Context(segments=merged, judge=self.judge)

    def judge_input(self) -> str:
        """判定侧唯一入口：**结构上拿不到 knowledge 段**。"""
        return self.judge


def assert_no_leak(ctx: Context, docs: Sequence[KnowledgeDoc]) -> None:
    """知识正文出现在任何非 L2.knowledge 段，或出现在 judge 输入里 → 抛。

    查的是**正文片段**而不是 id：id 出现在 L1 里只是提了一句"用过某篇知识"，
    正文出现在 L1 里才是知识变成了事实。
    """
    for d in docs:
        needle = d.body.strip()[:64]
        if not needle:
            continue
        if needle in ctx.judge_input():
            raise KnowledgeError(f"{d.id}: 知识正文进入判定输入（36 §4.1：knowledge 永不进判定）")
        for name, text in ctx.segments.items():
            if name == KNOWLEDGE_SEGMENT:
                continue
            if needle in text:
                raise KnowledgeError(f"{d.id}: 知识正文泄漏到 {name} 段")


# ══════════════════════════════════════════════════════════════════
# 判定与知识无关：可执行证明
# ══════════════════════════════════════════════════════════════════
def knowledge_free(judge: str, ctx: Context | None, docs: Sequence[KnowledgeDoc]) -> str:
    """判定结果的**唯一合法来源**：judge 入参。

    ``ctx``/``docs`` 只用来做泄漏断言，**不参与返回值**——这条签名本身就是红线的形状：
    想让 knowledge 影响判定，得先改这个函数的签名，那是 review 时一眼可见的改动。
    """
    if ctx is not None and docs:
        assert_no_leak(ctx, docs)
    return judge


def verdict_fingerprint(verdicts: Mapping[str, str]) -> str:
    """判定集合指纹：改知识前后必须逐字相同。"""
    payload = "|".join(f"{k}:{v}" for k, v in sorted(verdicts.items()))
    return _digest(payload)


__all__ = [
    "FORBIDDEN_SEGMENTS",
    "KNOWLEDGE_SEGMENT",
    "MAX_BYTES",
    "MAX_PER_STEP",
    "Context",
    "KnowledgeDoc",
    "KnowledgeError",
    "assert_no_leak",
    "knowledge_free",
    "load_dir",
    "render",
    "select",
    "verdict_fingerprint",
]
