"""B568 · 「量具失误形态清单」自己也要有闸门。

27 轮里至少 7 次，我报出的"缺陷"是假的——量具自己对被测接口做了错误假设。
`docs/delivery/instrument-failure-modes.md` 把那七次归纳成六种形态 + 三条硬规矩。

**但一份讲"怎么防止文档腐烂"的文档，自己也会腐烂。** 所以这里钉住三件事：

1. 清单里列的每个批次号，**在账本里真的存在**（不许写一个没发生过的案例）；
2. 六种形态**都有实例**（空条目的"形态"是想象出来的，不是踩出来的）；
3. 三条硬规矩**都在**（删掉一条就是悄悄放宽）。

第 1 条最重要：它让这份文档与账本**互相校验**——改账本不改文档会红，
写一条没发生过的案例也会红。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "delivery" / "instrument-failure-modes.md"
LEDGER = ROOT / ".hos-ares" / "batches.jsonl"


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _batches() -> set[str]:
    return {
        json.loads(line)["batch"]
        for line in LEDGER.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


# ── 1：文档里引用的每个批次必须真的存在 ─────────────────────
def test_every_referenced_batch_exists_in_the_ledger() -> None:
    """不许写一个没发生过的案例。

    这条让文档与账本互相校验：账本改了而文档留着旧号，或者凭空写一个号，都会红。
    """
    cited = set(re.findall(r"B(\d{2,3})", _doc()))
    known = {b.lstrip("B") for b in _batches()}
    missing = sorted(c for c in cited if c not in known)
    assert not missing, f"文档引用了账本里不存在的批次：{['B' + m for m in missing]}"


# ── 2：六种形态都必须有实例 ─────────────────────────────────
def test_all_six_forms_are_documented() -> None:
    """六种形态一个不许少（删一条就是悄悄放宽）。"""
    text = _doc()
    for form in "排序假设 截断假设 实例/身份假设 环境假设 参数化假设 列表/展开假设".split():
        assert form in text, f"缺形态：{form}"


def test_each_form_names_a_concrete_batch() -> None:
    """每种形态都要点名**它来自哪一批**——"通用建议"是想象，不是踩出来的。

    做法：按 `### N. xxx` 切块，每块里必须出现至少一个 `B<数字>`。
    """
    text = _doc()
    blocks = re.split(r"\n### \d+\. ", text)
    forms = [b for b in blocks if "**症状**" in b]
    assert forms, "没找到形态块"
    for block in forms:
        title = block.splitlines()[0].strip()
        assert re.search(r"B\d{2,3}", block), f"形态「{title}」没有实例批次"


# ── 3：三条硬规矩都在 ───────────────────────────────────────
def test_three_hard_rules_are_present() -> None:
    text = _doc()
    assert "规矩一：先写判据，再写量具" in text
    assert "规矩二：量具必须能红" in text
    assert "规矩三：量具的失败**不是结论**，是线索" in text


# ── 4：那份"七次假故障"的表格不许缩水 ───────────────────────
def test_the_false_alarm_table_has_the_real_entries() -> None:
    """表格是这份文档的证据基础：缩水 = 把踩过的坑重新忘一遍。"""
    text = _doc()
    rows = [ln for ln in text.splitlines() if ln.startswith("| B")]
    assert len(rows) >= 7, f"假故障表格只剩 {len(rows)} 行"
    for batch in "B539 B555 B556 B557 B565 B566".split():
        assert any(batch in r for r in rows), f"{batch} 的案例不见了"


# ── 5：不许写成"防止 AI 出错"的空话 ─────────────────────────
@pytest.mark.parametrize("vague", ["始终保持谨慎", "注意细节", "仔细检查", "提高质量意识"])
def test_no_empty_advice(vague: str) -> None:
    """清单的价值在**具体的错误形态**，不在"要小心"。

    空话会让人以为已经防住了，实际什么都没说。
    """
    assert vague not in _doc(), f"出现空话：{vague}"
