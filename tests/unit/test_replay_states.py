"""B563 · 「六态 replay」是**六种结论 + 一个初始值**，不是七个平级状态。

文档里到处写"六态"，而 `ReplayStatus` 枚举有 **7 个值**：

```
NEVER  OK  PARTIAL  FAILED  BLOCKED  ROUTE_UNRESOLVED  ROUTE_DRIFT
```

本批核实这两件事是否自洽（结论：**自洽**）：

- 规格 `06 §5.1` 列的六态是 `REPLAYED_OK` / `PARTIAL` / `ASSERTION_NOT_REPRODUCED` /
  `BLOCKED` / `ROUTE_UNRESOLVED` / `ROUTE_DRIFT`；
- `NEVER` 不在其中——它是**「还没重放过」**：DB 列的默认值，也是 `--mode dry` 的返回值。

换句话说：**六种结论，一个初始值**。这不是文档写错，是"六态"这个词指的是结论集。

为什么要写成用例：这两个数字（6 / 7）放在一起看像矛盾，下一个人很可能
"顺手"把文档改成七态，或者把 `NEVER` 从枚举里删掉。两种都会破坏语义。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hos_ares_core.models import ReplayStatus  # noqa: E402

#: 规格 `06 §5.1` 列的六种**结论**
SIX_VERDICTS = (
    ReplayStatus.OK,
    ReplayStatus.PARTIAL,
    ReplayStatus.FAILED,
    ReplayStatus.BLOCKED,
    ReplayStatus.ROUTE_UNRESOLVED,
    ReplayStatus.ROUTE_DRIFT,
)


def test_enum_has_exactly_seven_values() -> None:
    """7 个值 = 6 种结论 + 1 个初始值。数字本身要钉住，免得被"顺手"改掉。"""
    assert len(list(ReplayStatus)) == 7, [s.value for s in ReplayStatus]


def test_six_verdicts_plus_never_covers_the_enum() -> None:
    """六种结论 + `NEVER` 必须**恰好**等于全部枚举值（不多不少）。"""
    assert set(SIX_VERDICTS) | {ReplayStatus.NEVER} == set(ReplayStatus)


def test_never_is_the_only_non_verdict_state() -> None:
    """`NEVER` 是唯一"不是结论"的状态——它是"还没重放过"。"""
    non_verdict = set(ReplayStatus) - set(SIX_VERDICTS)
    assert non_verdict == {ReplayStatus.NEVER}, non_verdict


def test_never_is_the_db_default() -> None:
    """`NEVER` 同时是 `capsules.replay_status` 列的默认值（「还没跑过」）。

    这条把"它是初始值"从口头说明变成可查事实：新封存的 capsule 一定是 NEVER。
    """
    from hos_ares_core.store import DDL

    assert "'NEVER'" in DDL, "capsules 表的 replay_status 默认值不是 NEVER"


def test_dry_mode_returns_never() -> None:
    """`--mode dry` 不发包，结论必然还是 `NEVER`（带原因，不是静默）。

    这条挡的是"dry 模式假装验过"——那会让"没测"混进六态里。
    """
    src = (ROOT / "hos_ares_core" / "evidence.py").read_text(encoding="utf-8")
    assert "ReplayStatus.NEVER, \"dry_run\"" in src, "dry 模式的返回不是 (NEVER, dry_run)"


@pytest.mark.parametrize("status", SIX_VERDICTS)
def test_every_verdict_has_a_distinct_value(status: ReplayStatus) -> None:
    """六个结论的字符串值必须互不相同（DB 里靠它区分）。"""
    values = [s.value for s in SIX_VERDICTS]
    assert len(values) == len(set(values)), values
    assert status.value
