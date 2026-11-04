"""B49 任务级并行（进程内版）：并发跑多个任务，**结论必须与串行逐条相同**。

三条纪律（`H02 §6`）：

1. **并行单位是任务**，同任务内仍串行（保护可 replay）；
2. **上限 3**（`D-H-05`）；超了直接拒，不做"排队等会儿"的隐式降级；
3. **跨任务读=事故**：`TaskScope` 只允许读本任务的黑板事实，读别人的直接抛。

容器级隔离不在本模块（依赖 B45 的运行时）：本模块证明的是"进程内并发不污染结果"，
`isolation="process"` 显式写在结果里，不许被读成"容器隔离已证"。
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

#: 结果里的隔离级别（process = 进程内；container 需 B45）
ISOLATION = "process"


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


__all__ = [
    "ISOLATION",
    "MAX_PARALLEL",
    "ParallelError",
    "StageState",
    "TaskResult",
    "TaskScope",
    "findings_sha",
    "merge_sha",
    "run_parallel",
    "run_stages",
]
