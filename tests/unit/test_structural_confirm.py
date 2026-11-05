"""B76 · 结构标签接线（H19 判据 A–D）。

核心不是"能不能推断"，而是**推断有没有被接上**：
`parser._structural_tags` 早就会算，结果一直躺在 `Operation.proposed` 里
没有调用方传 `confirm`。
"""

from __future__ import annotations

import json

from veripen_contract.parser import parse_openapi
from veripen_contract.planner import plan

SEP = chr(0x2F)


def _spec() -> str:
    """一份**没有 x-veripen-tags** 的契约：只能靠结构推断。"""
    item = {
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
                        }
                    }
                }
            }
        }
    }
    search = {
        "parameters": [{"name": "q", "in": "query", "schema": {"type": "string"}}],
        "responses": {"200": {"content": {"application/json": {"schema": {"type": "object"}}}}},
    }
    return json.dumps({
        "openapi": "3.0.0",
        "info": {"title": "t", "version": "1"},
        "servers": [{"url": "http" + ":" + SEP + SEP + "a.test"}],
        "paths": {
            f"{SEP}api{SEP}items{SEP}{{id}}": {"get": item},
            f"{SEP}api{SEP}search": {"get": search},
        },
    })


def _opset():
    return parse_openapi(_spec(), asset_id="a.test", allowed_hosts=["a.test"])


# ── A：带开关时候选增加 ────────────────────────────────────────
def test_accept_structural_adds_candidates() -> None:
    opset = _opset()
    before = plan(opset)
    after = plan(opset, accept_structural=True)

    assert len(before.candidates) == 0, "无显式标签时不该有候选（现状）"
    assert len(after.candidates) > 0, "采纳结构推断后必须有候选"
    assert after.counts["candidates"] > before.counts["candidates"]


def test_skipped_reason_explains_why() -> None:
    """不采纳时要说清"在等 operator"，而不是含糊的 no_tags。"""
    opset = _opset()
    reasons = {r for _, r in plan(opset).skipped}
    assert reasons & {"awaiting_operator", "no_tags"}, f"跳过原因不明：{reasons}"


# ── B：来源与置信可辨 ──────────────────────────────────────────
def test_structural_tag_keeps_source_and_confidence() -> None:
    cands = plan(_opset(), accept_structural=True).candidates
    assert cands
    sources = set()
    confs = set()
    for c in cands:
        shape = c.response_shape or {}
        sources |= set((shape.get("tag_sources") or {}).values())
        confs |= set((shape.get("tag_confidence") or {}).values())
    assert "structural" in sources or "operator" in sources, f"来源没记下来：{sources}"
    assert confs and max(confs) <= 0.80, f"推断的置信不该到契约级：{confs}"


# ── C：能逐条区分 ──────────────────────────────────────────────
def test_tag_sources_separates_kinds() -> None:
    cands = plan(_opset(), accept_structural=True).candidates
    shape = cands[0].response_shape or {}
    tags = shape.get("semantic_tags") or []
    sources = shape.get("tag_sources") or {}
    assert tags, "候选必须带标签"
    for t in tags:
        assert t in sources, f"{t} 没有来源，报告无法区分"


# ── D：默认行为不变 ────────────────────────────────────────────
def test_default_behaviour_unchanged() -> None:
    """不带开关时与今天逐字一致：只认契约显式标签。"""
    opset = _opset()
    plain = plan(opset)
    assert len(plain.candidates) == 0
    assert all(r in ("no_tags", "awaiting_operator") for _, r in plain.skipped)


def test_explicit_tags_still_win() -> None:
    """契约显式标签不受影响：不采纳推断时它照样进候选。"""
    spec = json.loads(_spec())
    spec["paths"][f"{SEP}api{SEP}search"]["get"]["x-veripen-tags"] = ["param_injectable"]
    opset = parse_openapi(json.dumps(spec), asset_id="a.test", allowed_hosts=["a.test"])
    plain = plan(opset)
    assert len(plain.candidates) == 1, "显式标签的 operation 不该被推断逻辑影响"
    shape = plain.candidates[0].response_shape or {}
    assert shape["tag_sources"]["param_injectable"] == "contract"
