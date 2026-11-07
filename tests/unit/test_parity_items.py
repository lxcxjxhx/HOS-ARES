"""B141 · parity 的 E4 必须真的是绿的。

以前 E4 直接 `return SKIPPED, "资产发现暂按 D-H-03 维持不做"`——
能力矩阵在说假话。现在它是桩 fetch 走一轮的机械探针。

这条用例挡住"哪天又把它改回不做"。
"""

from __future__ import annotations


def test_e4_discovery_is_green_not_skipped() -> None:
    from veripen_cli.cmd_parity import run

    rows = {r["key"]: r for r in run()}
    e4 = rows["E4"]
    assert e4["status"] == "green", e4
    assert "不做" not in e4["detail"], e4["detail"]


def test_only_e1_remains_unverified() -> None:
    """除 E1（缺容器运行时）之外，不该有未验项。"""
    from veripen_cli.cmd_parity import run

    unverified = {r["key"] for r in run() if r["status"] == "unverified"}
    assert unverified <= {"E1"}, unverified


def test_no_item_is_marked_as_not_done() -> None:
    from veripen_cli.cmd_parity import run

    skipped = {r["key"] for r in run() if r["status"] == "skipped"}
    assert not skipped, f"仍标着不做的：{skipped}"
