"""B613 · `isolation` 的**默认值不许替调用方撒谎**。

## 改的是什么

`ContainerRun.isolation` 早先默认 `"container"`：

```python
isolation: str = "container"      # 忘了填 = 声称"容器隔离"
```

实测有个分支确实漏了（`except` 里抓单任务异常）。那条路径上容器**可能根本
没起来**（`pool.submit` 抛异常时连 `one()` 都没跑），但报告会写 `container`。

现在默认空串：漏填就是空，读起来是"不知道"，不是"是容器"。

## 判据

1. 默认值不能是 `container`（否则漏填即撒谎）
2. 非容器执行体 → `blocked` 且 `isolation` 如实反映执行体档位
3. 容器执行体的**异常分支**也要带 `isolation`（不能因为失败就丢标注）
4. 正常路径的 `isolation` 来自执行体，不是硬编码
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from hos_ares_core.parallel import (  # noqa: E402
    ContainerRun, ContainerTask, ParallelError, run_parallel_containers,
)


class _FailExecutor:
    """容器档执行体，但 `run` 抛异常 —— 用来走 `except` 分支。"""

    isolation = "container"

    def run(self, *a: object, **kw: object) -> object:
        raise RuntimeError("容器起不来")


class _JobObjectExecutor:
    """非容器档（Job Object）—— 必须被判 blocked，不许冒充。"""

    isolation = "job-object"

    def run(self, *a: object, **kw: object) -> object:      # pragma: no cover
        raise AssertionError("非容器执行体不该被调用")


def _tasks() -> list[ContainerTask]:
    return [ContainerTask("t-1", ("echo", "hi"))]


# ── 1：默认值不能声称容器隔离 ───────────────────────────────
def test_default_isolation_is_not_container() -> None:
    """**这条是根因判据。**

    默认值是 `container` 时，任何"忘了填"的构造都会声称容器隔离。
    那不是"没标注"，是**错误的标注**。
    """
    assert ContainerRun("t-1").isolation != "container", (
        "默认值声称了 container —— 忘了填的构造会撒谎"
    )


# ── 2：非容器执行体如实 blocked ─────────────────────────────
def test_job_object_executor_is_blocked_with_honest_label() -> None:
    runs = run_parallel_containers(_JobObjectExecutor(), _tasks())
    assert len(runs) == 1
    assert runs[0].outcome == "blocked"
    assert runs[0].isolation == "job-object", runs[0].isolation


# ── 3：异常分支也带 isolation ───────────────────────────────
def test_error_branch_still_carries_isolation() -> None:
    """容器档执行体抛异常时，`isolation` 仍要标出档位。

    区分两种失败：**"隔离了但任务挂了"** vs **"没隔离"**。
    前者可以重试，后者必须报 BLOCKED。
    """
    runs = run_parallel_containers(_FailExecutor(), _tasks())
    assert len(runs) == 1
    assert runs[0].outcome == "error"
    assert runs[0].isolation == "container", (
        f"异常分支丢了 isolation：{runs[0].isolation!r}"
    )


# ── 4：上限仍照旧 ──────────────────────────────────────────
def test_limit_still_enforced() -> None:
    many = [ContainerTask(f"t-{i}", ("echo",)) for i in range(5)]
    with pytest.raises(ParallelError):
        run_parallel_containers(_JobObjectExecutor(), many)
