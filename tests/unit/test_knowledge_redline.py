"""B12.4 knowledge 红线测试：**改知识，verdict 逐字不变**。

这条测试比加载器本身重要。加载器写错最多是少注入一条；
红线破了就是"装个插件能把结论掰弯"，那是整个插件体系崩掉的起点。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from veripen_core.context import (
    KNOWLEDGE_SEGMENT,
    MAX_BYTES,
    MAX_PER_STEP,
    Context,
    KnowledgeError,
    assert_no_leak,
    knowledge_free,
    load_dir,
    render,
    select,
    verdict_fingerprint,
)

KNOWLEDGE_DIR = Path(__file__).resolve().parents[2] / "knowledge"

#: 判定结果只由 pairs 决定：这里模拟三条判定，知识层不许碰到
BASE_JUDGEMENTS = {
    "P-01": "VERIFIED",
    "N-01": "REFUTED",
    "N-02": "INCONCLUSIVE",
}


@pytest.fixture(scope="module")
def docs():  # noqa: ANN201
    return load_dir(KNOWLEDGE_DIR)


# ══════════════════════════════════════════════════════════════════
# 1 红线：knowledge 不进判定
# ══════════════════════════════════════════════════════════════════
def test_knowledge_not_in_judge_path(docs) -> None:  # noqa: ANN001
    """DoD 点名的那条：**改 knowledge 全文后 verdict 逐字不变**。"""
    judge = "pairs:a>b>c"
    before = {k: knowledge_free(judge, None, ()) for k in BASE_JUDGEMENTS}
    ctx = Context(segments={"L1": "php+nginx", "L2": "上一步：发了 baseline"}, judge=judge)
    injected = ctx.with_knowledge(docs)
    after = {k: knowledge_free(judge, injected, docs) for k in BASE_JUDGEMENTS}

    assert before == after
    assert verdict_fingerprint(before) == verdict_fingerprint(after)


def test_judge_input_has_no_knowledge_after_injection(docs) -> None:  # noqa: ANN001
    ctx = Context(segments={"L1": "x"}, judge="pairs:1").with_knowledge(docs)
    for d in docs:
        assert d.body.strip()[:64] not in ctx.judge_input()


def test_leak_into_judge_raises(docs) -> None:  # noqa: ANN001
    needle = docs[0].body.strip()[:64]
    bad = Context(segments={}, judge=f"pairs + {needle}")
    with pytest.raises(KnowledgeError, match="判定输入"):
        assert_no_leak(bad, docs)


def test_leak_into_facts_segment_raises(docs) -> None:  # noqa: ANN001
    """知识正文一旦进了 L1，就变成了"我们已确认的事实"——这是最隐蔽的一种腐化。"""
    needle = docs[0].body.strip()[:64]
    bad = Context(segments={"L1": needle}, judge="pairs")
    with pytest.raises(KnowledgeError, match="泄漏到 L1"):
        assert_no_leak(bad, docs)


def test_wrong_gate_signature_would_be_needed_to_leak(docs) -> None:  # noqa: ANN001
    """knowledge_free 的签名里 judge 是唯一入参——想放知识进去必须改签名，review 一眼可见。"""
    ctx = Context(segments={}, judge="pairs").with_knowledge(docs)
    assert knowledge_free(ctx.judge_input(), ctx, docs) == "pairs"


# ══════════════════════════════════════════════════════════════════
# 2 加载与上限
# ══════════════════════════════════════════════════════════════════
def test_loads_docs_sorted_and_digested(docs) -> None:  # noqa: ANN001
    assert docs, "knowledge/ 下应至少有一篇"
    assert [d.id for d in docs] == sorted(d.id for d in docs)
    for d in docs:
        assert len(d.sha256) == 64
        assert d.bytes_used <= MAX_BYTES


def test_missing_dir_is_empty_not_error(tmp_path: Path) -> None:
    assert load_dir(tmp_path / "nope") == []


def test_oversized_doc_is_truncated_and_marked(tmp_path: Path) -> None:
    p = tmp_path / "big.md"
    p.write_text("汉" * 800, encoding="utf-8")  # 2400 字节
    got = load_dir(tmp_path)
    assert got[0].truncated is True
    assert got[0].bytes_used <= MAX_BYTES
    # 截断落在字符边界：没有半个 UTF-8 字符
    assert got[0].body.encode("utf-8").decode("utf-8") == got[0].body


def test_select_is_capped_at_two(docs) -> None:  # noqa: ANN001
    picked = select(docs)
    assert len(picked) <= MAX_PER_STEP
    assert MAX_PER_STEP == 2


def test_select_prefers_tagged_docs(docs) -> None:  # noqa: ANN001
    picked = select(docs, tags=[docs[-1].id])
    assert picked[0].id == docs[-1].id
    assert len(picked) <= MAX_PER_STEP


def test_select_with_unknown_tag_still_capped(docs) -> None:  # noqa: ANN001
    assert len(select(docs, tags=["does-not-exist"])) <= MAX_PER_STEP


# ══════════════════════════════════════════════════════════════════
# 3 注入形态
# ══════════════════════════════════════════════════════════════════
def test_render_carries_warning_prefix_and_ids(docs) -> None:  # noqa: ANN001
    body = render(docs[:2])
    assert body.startswith("[知识参考，非事实，不参与判定]")
    for d in docs[:2]:
        assert d.id in body


def test_empty_render_is_empty_and_no_segment_created() -> None:
    assert render(()) == ""
    ctx = Context(segments={"L1": "x"}, judge="p")
    assert KNOWLEDGE_SEGMENT not in ctx.with_knowledge(()).segments


def test_injection_lands_in_l2_knowledge_only(docs) -> None:  # noqa: ANN001
    ctx = Context(segments={"L1": "php"}, judge="p").with_knowledge(docs[:1])
    assert KNOWLEDGE_SEGMENT in ctx.segments
    assert ctx.segments["L1"] == "php"  # 原有段一个字节都没动


def test_label_does_not_leak_into_facts(docs) -> None:  # noqa: ANN001
    """id 出现在 L1 只是"用过某篇知识"，不算泄漏；正文出现才算。"""
    ctx = Context(segments={"L1": f"参考过 {docs[0].id}"}, judge="p").with_knowledge(docs[:1])
    assert_no_leak(ctx, docs[:1])  # 不抛
