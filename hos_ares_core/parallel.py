"""B49 任务级并行（进程内版）：并发跑多个任务，**结论必须与串行逐条相同**。

三条纪律（`H02 §6`）：

1. **并行单位是任务**，同任务内仍串行（保护可 replay）；
2. **上限 3**（`D-H-05`）；超了直接拒，不做"排队等会儿"的隐式降级；
3. **跨任务读=事故**：`TaskScope` 只允许读本任务的黑板事实，读别人的直接抛。

**容器级并行（B537）**：`run_parallel_containers` 让每个任务跑在**自己的容器**里，
互不共享文件系统与网络命名空间。判据与进程内版同形——并发的结果指纹必须与串行**逐条相同**。
两件事分开记：`isolation="process"` 是进程内（本模块的老部分），
`isolation="container"` 只在真起了容器时出现，**不许用前者冒充后者**。
"""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from .blackboard import Blackboard, Fact

#: 并行上限（D-H-05）
MAX_PARALLEL = 3

#: 结果里的隔离级别（process = 进程内；container = 每任务一容器）
ISOLATION = "process"

#: 容器级并行的默认镜像（与沙箱 attest 同口径）
CONTAINER_IMAGE = "python:3.13-slim"


class ParallelError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class TaskResult:
    task_id: str
    findings: tuple[Mapping[str, Any], ...] = ()
    steps: int = 0
    error: str = ""


def findings_sha(findings: Sequence[Mapping[str, Any]]) -> str:
    """规范化的发现集合指纹：按 (oracle, asset, verdict) 排序后哈希。"""
    rows = sorted(
        (f"{f.get('oracle', '')}|{f.get('asset', '')}|{f.get('verdict', '')}" for f in findings)
    )
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()


def run_parallel(runners: Mapping[str, Callable[[str], TaskResult]], *,
                 limit: int = MAX_PARALLEL) -> tuple[TaskResult, ...]:
    """并发跑任务，返回按 task_id 排序的结果（顺序与调度交错无关）。"""
    if len(runners) > limit:
        raise ParallelError("too_many_tasks", f"{len(runners)} > 上限 {limit}")
    if not runners:
        return ()
    with ThreadPoolExecutor(max_workers=min(limit, len(runners))) as pool:
        futures = {task_id: pool.submit(fn, task_id) for task_id, fn in runners.items()}
        results = []
        for task_id in sorted(futures):
            try:
                results.append(futures[task_id].result())
            except Exception as exc:  # noqa: BLE001 - 单任务失败不拖垮整体
                results.append(TaskResult(task_id=task_id, error=f"{type(exc).__name__}: {exc}"))
    return tuple(results)


def merge_sha(results: Sequence[TaskResult]) -> str:
    merged: list[Mapping[str, Any]] = []
    for result in results:
        merged.extend(result.findings)
    return findings_sha(merged)


class TaskScope:
    """任务作用域：读黑板时只能读自己的。**跨任务读直接抛**，不是过滤后返回空。"""

    def __init__(self, blackboard: Blackboard, task_id: str) -> None:
        self._bb = blackboard
        self.task_id = task_id

    def facts(self) -> tuple[Fact, ...]:
        return tuple(self._bb.live(self.task_id))

    def read(self, task_id: str) -> tuple[Fact, ...]:
        if task_id != self.task_id:
            raise ParallelError("cross_task_read",
                                f"{self.task_id} 试图读 {task_id} 的事实（事故，不是过滤）")
        return tuple(self._bb.live(task_id))


@dataclass
class StageState:
    stage: str = "init"
    done: list[str] = field(default_factory=list)
    failed: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"stage": self.stage, "done": list(self.done), "failed": self.failed,
                "isolation": ISOLATION}


def run_stages(stages: Sequence[tuple[str, Callable[[], None]]], *,
               budget: dict[str, Any] | None = None) -> StageState:
    """阶段级推进：某阶段抛错 → 回退到上一阶段，**预算不重算**（同一个 dict，值不变）。"""
    state = StageState(stage="init")
    spent = budget if budget is not None else {}
    for name, step in stages:
        try:
            step()
        except Exception as exc:  # noqa: BLE001 - 失败是状态，不是崩溃
            state.failed = f"{name}: {type(exc).__name__}: {exc}"
            state.stage = state.done[-1] if state.done else "init"
            return state
        state.done.append(name)
        state.stage = name
        spent[name] = spent.get(name, 0) + 1     # 记账是"这一步跑过"，不是"重算预算"
    return state


@dataclass(frozen=True)
class ContainerTask:
    """一个跑在**自己容器里**的任务（B537）。

    判据与进程内版同形：并发的结果指纹必须与串行**逐条相同**。
    区别在隔离事实——每个任务一个容器，文件系统与网络命名空间互不可见。
    """

    task_id: str
    #: 容器内要跑的命令（镜像里的可执行 + 参数）
    argv: tuple[str, ...]
    findings: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class ContainerRun:
    """容器级并行的结果。`isolation` 只在真起了容器时才是 `container`。

    ## 默认值为什么是空串（B613）

    早先是 `isolation: str = "container"` —— 那样**忘了填就等于声称"容器隔离"**。
    实测有个分支（`except` 里抓单任务异常）确实没填：

    ```python
    out.append(ContainerRun(task_id, outcome="error", reason=...))   # isolation 漏了
    ```

    而那条路径上容器**可能根本没起来**（`pool.submit` 抛异常时连 `one()` 都没跑）。
    于是报告里会写 `isolation="container"`，实际什么隔离都没有。

    **默认值不能替调用方撒谎。** 改成空串后，漏填就是空 —— 读起来是"不知道"，
    而不是"是容器"。空串在报告侧按"未标注"处理。
    """

    task_id: str
    exit_code: int | None = None
    stdout: str = ""
    outcome: str = "ok"
    reason: str = ""
    isolation: str = ""


def run_parallel_containers(
    executor: Any,
    tasks: Sequence[ContainerTask],
    *,
    image: str = CONTAINER_IMAGE,
    limit: int = MAX_PARALLEL,
    timeout_s: int = 300,
) -> tuple[ContainerRun, ...]:
    """每个任务一个容器，并发跑；返回按 task_id 排序（顺序与调度交错无关）。

    **上限与进程内版同一个数**（`D-H-05` 的 3）：容器贵，但纪律不该因为贵而放松。
    超了直接抛，不排队——"悄悄串行"会让调用方以为自己在并行。

    没有容器档执行体时**不降级到进程内**：那是把"没隔离"伪装成"隔离了"。
    调用方拿到 `outcome="blocked"`，自己决定是报 BLOCKED 还是换路。
    """
    if len(tasks) > limit:
        raise ParallelError("too_many_tasks", f"{len(tasks)} > 上限 {limit}")
    if not tasks:
        return ()
    if getattr(executor, "isolation", "") != "container":
        # 执行体不是容器档：如实 blocked，不许拿 Job Object 冒充
        return tuple(
            ContainerRun(task.task_id, outcome="blocked",
                         reason=f"executor_isolation={getattr(executor, 'isolation', '')!r}",
                         isolation=str(getattr(executor, "isolation", "")))
            for task in sorted(tasks, key=lambda t: t.task_id)
        )

    def one(task: ContainerTask) -> ContainerRun:
        res = executor.run(list(task.argv), image=image, timeout_s=timeout_s)
        return ContainerRun(
            task_id=task.task_id,
            exit_code=res.exit_code,
            stdout=res.stdout,
            outcome=res.outcome,
            reason=res.reason,
            isolation=str(res.isolation),
        )

    with ThreadPoolExecutor(max_workers=min(limit, len(tasks))) as pool:
        futures = {task.task_id: pool.submit(one, task) for task in tasks}
        out: list[ContainerRun] = []
        for task_id in sorted(futures):
            try:
                out.append(futures[task_id].result())
            except Exception as exc:  # noqa: BLE001 - 单任务失败不拖垮整体
                # `isolation` 显式填**执行体**的档位，不是"容器起没起来"：
                # 走到这里时容器可能压根没启动，但"用的是容器档执行体"这个事实
                # 仍然成立，报告侧需要它来区分"隔离了但任务失败"与"没隔离"。
                out.append(ContainerRun(
                    task_id, outcome="error",
                    reason=f"{type(exc).__name__}: {exc}",
                    isolation=str(getattr(executor, "isolation", ""))))
    return tuple(out)


__all__ = [
    "CONTAINER_IMAGE",
    "ContainerRun",
    "ContainerTask",
    "ISOLATION",
    "MAX_PARALLEL",
    "ParallelError",
    "StageState",
    "TaskResult",
    "TaskScope",
    "findings_sha",
    "merge_sha",
    "run_parallel",
    "run_parallel_containers",
    "run_stages",
]
