"""B217：交付物里的「影响」「建议」必须是人话，且由真实元数据推出来。"""

from __future__ import annotations

from pathlib import Path

import pytest

from veripen.report import advice_text, impact_text
from veripen.report.render import WORDING


def test_read_only_impact_says_data_was_not_changed() -> None:
    text = impact_text("read_only")
    assert "未被改动" in text and "信息泄露" in text


def test_reversible_write_impact_mentions_rollback() -> None:
    assert "回滚" in impact_text("reversible_write")


def test_unknown_impact_admits_it() -> None:
    """未知写入等级不许编：如实说未声明。"""
    assert "未声明" in impact_text("")
    assert "未声明" in impact_text("weird_class")


@pytest.mark.parametrize("sev", ["high", "medium", "low"])
def test_advice_points_at_a_verifiable_next_step(sev: str) -> None:
    text = advice_text(sev)
    assert "重跑本 oracle" in text and "REFUTED" in text


def test_advice_for_unknown_severity_still_verifiable() -> None:
    assert "REFUTED" in advice_text("")


def test_generated_texts_respect_the_wording_deny_list() -> None:
    """生成的文本不许踩 `lint.py` 的禁用词（verified_strong 禁 可能/疑似/建议关注）。"""
    deny = tuple(WORDING["verified_strong"]["deny"])
    blob = " ".join([impact_text("read_only"), impact_text("reversible_write"),
                     advice_text("high"), advice_text("medium"), advice_text("low")])
    for word in deny:
        assert word not in blob, f"生成文本里出现禁用词：{word}"


def test_run_and_demo_both_use_the_helper() -> None:
    """真跑与 demo 两条出口都必须用同一套（否则又是两份说法）。"""
    root = Path(__file__).resolve().parents[2]
    for rel in ("veripen_cli/cmd_run.py", "scripts/demo.py"):
        src = (root / rel).read_text(encoding="utf-8")
        assert "impact_text(" in src and "advice_text(" in src, rel
        assert "见 capsule" not in src, f"{rel} 还留着占位符"


# ── B218：现象要说观测到什么；指纹不许印零 ─────────────────────
def test_every_tier_a_judge_emits_a_statement() -> None:
    """四个 judge 都要给「现象」一句话——缺了，交付物里只能写"判定成立"。"""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "veripen_oracles" / "tierA.py").read_text(
        encoding="utf-8")
    blocks = src.count('"kind": "')
    with_statement = src.count('"statement":')
    assert blocks >= 4, blocks
    assert with_statement >= blocks, f"assertion 块 {blocks} 个，带 statement 的只有 {with_statement} 个"


def test_zero_fingerprint_is_not_rendered_as_a_hash() -> None:
    from veripen.report.render import _root_fingerprint_line

    zero = _root_fingerprint_line("0" * 64)
    assert "未计算" in zero and "0" * 16 not in zero
    real = _root_fingerprint_line("4b0640801e8e8fbe" + "0" * 48)
    assert "4b0640801e8e8fbe" in real and "未计算" not in real
    assert "未计算" in _root_fingerprint_line("")


def test_demo_no_longer_prints_a_zero_root() -> None:
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "scripts" / "demo.py").read_text(encoding="utf-8")
    assert '"0" * 64' not in src, "demo 又在拿全零当根指纹"
    assert "chain.self_sha256" in src
