"""B134 · 判定能力的机械门槛：bench 必须一直达标。

现在 recall/precision 是"跑一次看一眼的数"——某次改动把 precision 从 1.00 打到 0.9，
没有任何东西会红。这组用例把门槛钉死：

| 门槛 | 值 | 为什么是这个数 |
|---|---|---|
| precision | **必须 1.00** | 报出来的每条都必须成立；假阳性比漏报更伤 |
| 越界 | **必须 0** | 出界即纪律破防 |
| error / not_run | 0 / 0 | 跑不动要说话，不能算进"没发现" |
| closure | ≥ 0.60 | 太低是瞎打，太高要怀疑只挑软柿子（H08） |
| recall | ≥ 0.90 | 当前 0.93；下方留出 0.03 波动，再低就说明能力退化 |

**不断言 recall == 1.00**：`rate_limit_absent` 按设计默认关闭，它那两条本来就是 INCONCLUSIVE。
把"取不到的"算成缺陷会逼着人打开一个拖慢整个 bench 的 oracle。
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "b12" / "run_bench.py"


@pytest.fixture(scope="module")
def bench(tmp_path_factory: pytest.TempPathFactory) -> dict:  # noqa: ANN401
    """跑两轮 bench（第二轮用来确认 routeset 与判定分布可复现）。"""
    out = tmp_path_factory.mktemp("bench")
    sys.path.insert(0, str(SCRIPT.parent))
    sys.path.insert(0, str(ROOT))
    from scripts.b12.run_bench import main as bench_main  # noqa: PLC0415

    rc = bench_main(["--rounds", "2", "--out", str(out)])
    assert rc == 0, f"bench 自己失败了（rc={rc}）"
    return json.loads((out / "bench.json").read_text(encoding="utf-8"))


def test_hard_gates_pass(bench: dict) -> None:
    """gates 已经包含 FP=0 / 越界=0 / routeset 一致 / 分布一致。"""
    assert bench["gates"]["ok"] is True, bench["gates"]["failures"]


def test_precision_is_perfect(bench: dict) -> None:
    for row in bench["rounds"]:
        assert row["precision"] == 1.0, f"第 {row['round']} 轮出现假阳性"


def test_no_errors_and_no_silent_skips(bench: dict) -> None:
    counts = bench["totals"]["by_outcome"]
    assert counts["error"] == 0, counts
    assert counts["not_run"] == 0, counts


def test_recall_floor(bench: dict) -> None:
    for row in bench["rounds"]:
        assert row["recall"] >= 0.90, f"第 {row['round']} 轮召回掉到 {row['recall']}"


def test_closure_rate_in_sane_band(bench: dict) -> None:
    for row in bench["rounds"]:
        rate = row["closure_rate"]
        assert 0.60 <= rate <= 1.0, f"闭环率 {rate} 不在合理区间"


def test_routeset_is_reproducible(bench: dict) -> None:
    shas = {row["routeset"] for row in bench["rounds"]}
    assert len(shas) == 1, f"两轮 routeset 不一致：{shas}"


def test_boundaries_are_zero(bench: dict) -> None:
    assert bench["totals"]["boundaries"] == 0
