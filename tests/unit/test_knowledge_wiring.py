"""B63 · plugin knowledge 进 L2（H11 判据 A–E）。

两条红线：
1. knowledge **只能**出现在 `L2.knowledge` 段，判定侧结构上拿不到；
2. 卸载即消失——`Lifecycle.knowledge` 是唯一真相源，不另存缓存。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from veripen_core.briefing import build_knowledge, knowledge_text
from veripen_core.context import (
    KNOWLEDGE_SEGMENT,
    MAX_BYTES,
    MAX_PER_STEP,
    KnowledgeError,
    knowledge_free,
    verdict_fingerprint,
)
from veripen_core.plugins.lifecycle import Lifecycle, PluginSpec
from veripen_core.tools.surface import build_surface

REPO = Path(__file__).resolve().parents[2]


def _plugin(name: str = "kb", docs: tuple[str, ...] = ("doc-1",)) -> PluginSpec:
    return PluginSpec(name=name, version="1.0.0", mount="knowledge", knowledge=docs)


def _lc() -> Lifecycle:
    return Lifecycle(surface=build_surface())


# ── A：生产调用 ────────────────────────────────────────────────
def test_with_knowledge_has_production_caller() -> None:
    briefing = (REPO / "veripen_core" / "briefing.py").read_text(encoding="utf-8")
    hunt = (REPO / "veripen_cli" / "cmd_hunt.py").read_text(encoding="utf-8")
    assert "with_knowledge(" in briefing, "briefing 必须真正调用 with_knowledge"
    assert "build_knowledge(" in hunt, "cmd_hunt 必须调用 build_knowledge"


# ── B：装 100 条不影响判定 ─────────────────────────────────────
def test_hundred_docs_do_not_change_verdict() -> None:
    lc = _lc()
    lc.install(_plugin("bulk", tuple(f"fact-{i}" for i in range(100))))
    docs = lc.knowledge_docs()
    assert len(docs) == 100

    ctx = build_knowledge(docs=docs)
    picks = [seg for name, seg in ctx.segments.items() if name == KNOWLEDGE_SEGMENT]
    assert len(picks) == 1, "只允许一个 knowledge 段"

    verdicts = {"sqli": "CONFIRMED", "xss": "REJECTED"}
    plain = verdict_fingerprint(verdicts)
    guarded = verdict_fingerprint(
        {k: knowledge_free(v, ctx, docs) for k, v in verdicts.items()}
    )
    assert guarded == plain


# ── C：改全文也不影响 ──────────────────────────────────────────
def test_body_change_keeps_fingerprint() -> None:
    lc = _lc()
    lc.install(_plugin("kb", ("原文：用 time-based 判 SQLi",)))
    before = build_knowledge(docs=lc.knowledge_docs())

    lc.install(_plugin("kb2", ("改写：用 bool-based 判 SQLi",)))
    after = build_knowledge(docs=lc.knowledge_docs())

    assert knowledge_text(before) != knowledge_text(after), "正文必须真的变了"
    verdicts = {"a": "CONFIRMED"}
    assert (
        verdict_fingerprint({k: knowledge_free(v, before, lc.knowledge_docs()) for k, v in verdicts.items()})
        == verdict_fingerprint({k: knowledge_free(v, after, lc.knowledge_docs()) for k, v in verdicts.items()})
    )


# ── D：限额与截断 ──────────────────────────────────────────────
def test_at_most_two_per_step() -> None:
    lc = _lc()
    lc.install(_plugin("many", tuple(f"d{i}" for i in range(10))))
    ctx = build_knowledge(docs=lc.knowledge_docs())
    inside = sum(1 for line in knowledge_text(ctx).splitlines() if line.startswith("- "))
    assert inside <= MAX_PER_STEP


def test_oversize_truncated_and_flagged() -> None:
    lc = _lc()
    big = "x" * (MAX_BYTES + 500)
    lc.install(_plugin("big", (big,)))
    doc = lc.knowledge_docs()[0]
    assert doc.truncated is True
    assert doc.bytes_used <= MAX_BYTES
    assert "已截断" in knowledge_text(build_knowledge(docs=[doc]))


# ── E：卸载即消失 ──────────────────────────────────────────────
def test_uninstall_removes_from_l2() -> None:
    lc = _lc()
    lc.install(_plugin("kb", ("只此一条",)))
    assert "只此一条" in knowledge_text(build_knowledge(docs=lc.knowledge_docs()))

    lc.uninstall("kb")
    ctx = build_knowledge(docs=lc.knowledge_docs())
    assert knowledge_text(ctx) == ""
    assert lc.knowledge_docs() == []


def test_no_leak_when_body_tries_judge_segment() -> None:
    """正文里出现判定串也必须留在 L2，`knowledge_free` 只看 judge 参数。"""
    lc = _lc()
    lc.install(_plugin("kb", ("verdict=CONFIRMED",)))
    ctx = build_knowledge(docs=lc.knowledge_docs())
    assert knowledge_free("pairs:1", ctx, lc.knowledge_docs()) == "pairs:1"


def test_empty_docs_returns_bare_context() -> None:
    ctx = build_knowledge()
    assert knowledge_text(ctx) == ""
    assert ctx.judge_input() == ""
