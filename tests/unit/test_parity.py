"""B59 三对标总验收（`H07` B59 / `H08` A17–A20）。

判据：22 项齐全且键唯一 · 状态只有四种 · **本机缺条件的项必须是 unverified 而不是 green** ·
出现 red 时命令退出码非 0 · 证据落盘。
"""

from __future__ import annotations

import json

import pytest

from veripen_cli import cmd_parity
from veripen_cli.cmd_parity import GREEN, RED, SKIPPED, UNVERIFIED, ITEMS, main, run

EXPECTED_KEYS = (
    "V1 V2 V3 V4 E1 E2 E3 E4 E5 P1 P2 P3 P4 P5 P6 P7 " "W1 W2 W3 G1 G2 G3"
).split()


def test_all_twenty_two_items_present_and_unique() -> None:
    keys = [item.key for item in ITEMS]
    assert keys == EXPECTED_KEYS
    assert len(set(keys)) == 22


def test_every_item_reports_one_of_four_states() -> None:
    rows = run()
    assert len(rows) == 22
    assert {r["status"] for r in rows} <= {GREEN, UNVERIFIED, SKIPPED, RED}
    assert all(r["detail"].strip() for r in rows), "每一项都要有说明，不许空着"


def test_missing_runtime_items_are_unverified_not_green() -> None:
    """无 docker / 无并行任务 → 未验；渲染执行体已实现（宿主）→ 绿但注明容器未验。"""
    by_key = {row["key"]: row for row in run()}
    assert by_key["E1"]["status"] == UNVERIFIED, "无 docker：真隔离只能未验"
    assert by_key["E4"]["status"] == SKIPPED, "资产发现是决策不做，不是没做"
    assert by_key["E2"]["status"] == GREEN and "容器内未验" in by_key["E2"]["detail"]
    assert by_key["E5"]["status"] == GREEN and "容器级并行未验" in by_key["E5"]["detail"]


def test_no_red_on_this_machine() -> None:
    assert [r for r in run() if r["status"] == RED] == []


def test_red_item_makes_command_fail(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    bad = (cmd_parity.Item("Z1", "测试", lambda: (RED, "故意失败")),)
    monkeypatch.setattr(cmd_parity, "ITEMS", bad)
    out = str(tmp_path / "parity.json")  # type: ignore[operator]
    assert main(["--json", "--out", out]) == 1


def test_json_evidence_is_written(tmp_path: object) -> None:
    out = str(tmp_path / "parity.json")  # type: ignore[operator]
    assert main(["--json", "--out", out]) == 0
    payload = json.loads((tmp_path / "parity.json").read_text(encoding="utf-8"))  # type: ignore[operator]
    assert payload["total"] == 22 and set(payload["counts"]) == {GREEN, UNVERIFIED, SKIPPED, RED}


def test_assertion_table_has_twenty_rows() -> None:
    """A20 的落点：权威表里有 A20；A18 有命令但只标宿主已验（隔离未验）。"""
    text = (cmd_parity.ROOT / "plan-veripen" / "16-完整范围与交付地图.md").read_text(encoding="utf-8")
    assert "A20 三对标可证" in text
    assert "A18 浏览器可证" in text and "容器内渲染未验" in text
