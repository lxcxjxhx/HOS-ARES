"""B549 · 靶场召回量具 + 请求预算可调。

## 为什么需要这个量具

`H05 §2` 的「真实资产召回 0.33」测的是 Firing Range 这类**外部**目标，本机访问不到，
那个数字只能原样引用。但有一条**本机可测**的等价物：

    靶场自己声明 24 个面 → 其中哪些是本产品明确能判的（可达 oracle）
    → 跑一轮 → 看可达的里面有多少真的产出了判定

它测的不是同一件事，而是**同一条链路的另一半**：外部目标召回低有三个原因
（OOB 缺域名 / 端点不匹配 / 标签没被采纳），后两条在靶场上不存在。
所以这个数回答的是：**在"目标配合"的前提下，这条链路自己能拿到多少。**

## 第一轮测出来的东西（0.419）

原因分布里有一条**不是"目标不配合"**：

```
10 条 oracle_crash:BudgetExhausted
```

`build_guard` 里写的是 `Budget()`（默认 200 次请求），**没有任何入口能调**。
一轮 24 个面 × 多 oracle 撞满，撞满的 oracle 报"预算耗尽"——
看起来像"判定不了"，其实是"预算没给够"。

修掉之后（`--max-requests` + 交上靶场自己的 token/符号表）：

```
召回 0.419 → 0.548     reported 24 → 30     BudgetExhausted 10 → 0
```
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))


# ── 1：预算必须可调（这批的修复）──────────────────────────────
def test_budget_default_is_unchanged() -> None:
    """默认仍是 200：改默认等于悄悄放宽所有既有部署的出网量。"""
    from hos_ares_core.guard.budget import Budget

    assert Budget().requests.max == 200
    assert Budget().steps.max == 120


def test_budget_accepts_an_explicit_request_cap() -> None:
    from hos_ares_core.guard.budget import Budget

    b = Budget(requests=2000)
    assert b.requests.max == 2000
    for _ in range(1999):
        b.spend_request()
    assert not b.exhausted
    b.spend_request()
    assert b.exhausted, "第 2000 次之后必须耗尽"


def test_build_guard_passes_the_cap_through() -> None:
    """`build_guard` 是 `hunt` 与 `run` 的**唯一**构造点：cap 必须从这里进去。"""
    import inspect

    from hos_ares_cli import cmd_hunt

    src = inspect.getsource(cmd_hunt.build_guard)
    assert "max_requests" in src
    assert "Budget(requests=max_requests)" in src


@pytest.mark.parametrize("module", ["hos_ares_cli.cmd_run", "hos_ares_cli.cmd_hunt"])
def test_both_entry_points_expose_max_requests(module: str) -> None:
    """同一条命令族上缺一个开关，就会出现「同一份目标在两条命令下预算不同」。"""
    import importlib
    import inspect

    src = inspect.getsource(importlib.import_module(module))
    assert '"--max-requests"' in src, f"{module} 缺 --max-requests"
    assert "max_requests=int(getattr(args" in src, f"{module} 的开关没接进 build_guard"


#: 真跑整轮 `run` 约 10 分钟/次。常规闸门里跳过，要验时：
#:   ``HOS_ARES_SLOW_RECALL=1 pytest tests/integration/test_recall_measurement.py``
#: 为什么不在闸门里跑：它验的是"量具算得对"，不是"产品没坏"——
#: 每次 CI 多花 20 分钟换一个数字，不值。
slow = pytest.mark.skipif(
    not __import__("os").environ.get("HOS_ARES_SLOW_RECALL"),
    reason="真跑整轮的召回量具（约 10 分钟/次）；设 HOS_ARES_SLOW_RECALL=1 才跑",
)


# ── 2：量具本身（真跑一轮，算得对）───────────────────────────
@slow
def test_recall_measurement_is_computable_and_monotone() -> None:
    """真起靶场跑一轮：召回必须落在 (0, 1] 且**不低于**无凭证时的值。

    为什么钉"不低于"：交上 token 与符号表只可能让更多 oracle 判得出来，
    如果反而变低，说明符号表把某个 oracle 引到了错误的面上——那是 bug，不是波动。
    """
    from lab.app import App
    from lab.net_server import serve

    sys.path.insert(0, str(ROOT / "scripts"))
    from measure_recall import measure  # noqa: PLC0415

    out = ROOT / "out" / "recall-test"
    out.mkdir(parents=True, exist_ok=True)

    import contextlib

    gen = serve(App())
    base = next(gen)
    try:
        plain = measure(base, out_dir=out / "plain", with_credentials=False, requests=2000)
        full = measure(base, out_dir=out / "full", with_credentials=True, requests=2000)
    finally:
        with contextlib.suppress(StopIteration):
            next(gen)

    assert 0.0 < plain["recall_on_lab"] <= 1.0, plain["recall_on_lab"]
    assert 0.0 < full["recall_on_lab"] <= 1.0, full["recall_on_lab"]
    assert full["recall_on_lab"] >= plain["recall_on_lab"], (
        f"交凭证后反而更低：{plain['recall_on_lab']} → {full['recall_on_lab']}"
    )
    assert full["oracles_judged"], "一条都没判出来"


@slow
def test_budget_exhaustion_disappears_with_a_bigger_cap() -> None:
    """这一批最直接的回归：默认预算下的 `BudgetExhausted` 在放宽后必须消失。"""
    from lab.app import App
    from lab.net_server import serve

    sys.path.insert(0, str(ROOT / "scripts"))
    from measure_recall import measure  # noqa: PLC0415

    import contextlib
    import json

    out = ROOT / "out" / "recall-test"
    out.mkdir(parents=True, exist_ok=True)
    gen = serve(App())
    base = next(gen)
    try:
        measure(base, out_dir=out / "cap", with_credentials=True, requests=2000)
    finally:
        with contextlib.suppress(StopIteration):
            next(gen)

    findings = json.loads((out / "cap" / "run" / "findings.json").read_text(encoding="utf-8"))
    exhausted = [
        f for f in findings["findings"]
        if "BudgetExhausted" in str(f.get("reason_code") or "")
    ]
    assert not exhausted, f"放宽预算后仍然撞满：{[f['oracle'] for f in exhausted]}"
