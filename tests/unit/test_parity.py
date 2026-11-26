"""B59 三对标总验收（`H07` B59 / `H08` A17–A20）。

判据：22 项齐全且键唯一 · 状态只有四种 · **本机缺条件的项必须是 unverified 而不是 green** ·
出现 red 时命令退出码非 0 · 证据落盘。
"""

from __future__ import annotations

import json

import pytest

from hos_ares_cli import cmd_parity
from hos_ares_cli.cmd_parity import GREEN, RED, SKIPPED, UNVERIFIED, ITEMS, main, run

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


def test_runtime_gaps_are_unverified_and_discovery_is_done() -> None:
    """E1 的真隔离只有**守护进程活着**才准绿；否则必须未验（B200）。"""
    by_key = {row["key"]: row for row in run()}
    # 硬写"必须未验"在两种环境里都是错的：本机没 docker（未验），
    # GitHub 的 windows runner 有活着的引擎（那是真能跑）。
    # 判据换成不变式：绿 ⇒ 报告里必须说明是哪个运行时，并且不是靠"命令存在"给的绿。
    e1 = by_key["E1"]
    if e1["status"] == GREEN:
        assert "运行时" in e1["detail"], e1["detail"]
        assert "守护进程" not in e1["detail"], e1["detail"]
    else:
        assert e1["status"] == UNVERIFIED, e1["status"]
    assert by_key["E4"]["status"] == GREEN, "资产发现已实现（B86 起），不该再标不做"
    assert by_key["E2"]["status"] == GREEN and "容器内未验" in by_key["E2"]["detail"]
    # E5 从 B537 起两半都可能：有容器档执行体就验到容器级，没有就只有进程内那半。
    # 判据是**说出来的是哪一半**，而不是写死某一个状态。
    e5 = by_key["E5"]
    assert e5["status"] == GREEN, e5
    assert "同指纹" in e5["detail"], e5["detail"]
    if "容器级并发" in e5["detail"]:
        assert "逐条相同" in e5["detail"], e5["detail"]
    else:
        assert "容器级并行未验" in e5["detail"], e5["detail"]


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
    text = (cmd_parity.ROOT / "plan-hos-legacy" / "16-完整范围与交付地图.md").read_text(encoding="utf-8")
    assert "A20 三对标可证" in text
    assert "A18 浏览器可证" in text and "容器内渲染未验" in text


# ── B251：每项要能看到"它是怎么被判的" ────────────────────────────
def test_every_item_says_where_its_judgement_lives() -> None:
    """这一层没有 shell 命令可写：每项是内置探针。至少要给出**判据位置**。"""
    for item in ITEMS:
        how = item.how
        assert how.startswith("cmd_parity."), (item.key, how)
        assert item.probe.__name__ in how, (item.key, how)


def test_json_rows_carry_the_judgement_locator(tmp_path: object) -> None:
    out = str(tmp_path / "parity.json")  # type: ignore[operator]
    assert main(["--json", "--out", out]) == 0
    payload = json.loads((tmp_path / "parity.json").read_text(encoding="utf-8"))  # type: ignore[operator]
    for row in payload["items"]:
        assert row.get("how", "").startswith("cmd_parity."), row


def test_human_output_prints_the_locator(capsys, tmp_path: object) -> None:  # noqa: ANN001
    assert main(["--out", str(tmp_path / "p.json")]) == 0  # type: ignore[operator]
    out = capsys.readouterr().out
    assert "└ cmd_parity._pure_judge" in out, out[:400]


# ── B252：判据位置要带一句说明，不能只有函数名 ────────────────────
def test_every_locator_carries_a_sentence() -> None:
    """`how` 只有 `cmd_parity._xxx` 时，读者仍不知道它在判什么。"""
    missing = [item.key for item in ITEMS if "——" not in item.how]
    assert not missing, f"这些项的判据位置没有说明：{missing}"


def test_docstrings_do_not_overclaim() -> None:
    """说明要跟实现对得上：`_evidence` 只做导入，不许写成"校验通过"。"""
    item = next(i for i in ITEMS if i.key == "V2")
    assert "导入" in item.how and "不跑" in item.how, item.how


# ── B253：组得出来 ≠ 过得了自身校验 ──────────────────────────────
def test_delivery_says_whether_the_package_passes_its_own_verify() -> None:
    """本机没有构建机的 wheels/ 与 images/，所以 verify 必报这两条。

    这一项判的是"能不能组出来"（green），但明细必须写明"自身校验未过"——
    否则读者会以为包已经可交付。
    """
    item = next(i for i in ITEMS if i.key == "G2")
    status, detail = item.probe()
    assert status == GREEN, detail
    assert "自身校验" in detail, detail
    if "未过" in detail:
        assert "wheels" in detail or "images" in detail, "要把缺什么写出来"


def test_delivery_docstring_does_not_promise_a_pass() -> None:
    item = next(i for i in ITEMS if i.key == "G2")
    assert "组得出来 ≠ 过得了校验" in item.how, item.how
