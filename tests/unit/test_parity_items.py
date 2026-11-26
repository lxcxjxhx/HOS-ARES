"""B141 · parity 的 E4 必须真的是绿的。

以前 E4 直接 `return SKIPPED, "资产发现暂按 D-H-03 维持不做"`——
能力矩阵在说假话。现在它是桩 fetch 走一轮的机械探针。

这条用例挡住"哪天又把它改回不做"。
"""

from __future__ import annotations


def test_e4_discovery_is_green_not_skipped() -> None:
    from hos_ares_cli.cmd_parity import run

    rows = {r["key"]: r for r in run()}
    e4 = rows["E4"]
    assert e4["status"] == "green", e4
    assert "不做" not in e4["detail"], e4["detail"]


def test_unverified_items_are_only_the_environment_gaps() -> None:
    """未验项只能是**依赖本机环境**的那几个，不能是产品缺口。

    E1（容器隔离）、E5（容器级并行）都要真起容器。没有容器档执行体的机器上
    它们会记 `unverified`——那是 D3 要的形状（**没能验 ≠ 行为坏**），
    所以这里不能硬写"必须只有一个"。

    挡的是另一件事：某个**纯本机可验**的能力项（V1/V2/V3/P1…）掉进 unverified，
    说明它退化成了"需要有环境才知道对不对"，那才是真的缺口。
    """
    from hos_ares_cli.cmd_parity import run

    env_gaps = {"E1", "E5"}
    unverified = {r["key"] for r in run() if r["status"] == "unverified"}
    assert unverified <= env_gaps, f"这几项不是环境缺口，是产品缺口：{unverified - env_gaps}"


def test_no_item_is_marked_as_not_done() -> None:
    from hos_ares_cli.cmd_parity import run

    skipped = {r["key"] for r in run() if r["status"] == "skipped"}
    assert not skipped, f"仍标着不做的：{skipped}"
