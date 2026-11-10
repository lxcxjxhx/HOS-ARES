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
