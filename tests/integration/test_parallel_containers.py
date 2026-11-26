"""B537 · 容器级并行（E5 的另一半）。

进程内并发已经验过（三任务指纹与串行一致），缺的一直是"**每任务一容器**"。
这一批补上，并且用同一个判据：并发的结果必须和串行**逐条相同**。

三条要钉住的事：

1. **真起了容器**（`isolation="container"`），不是进程内假装；
2. **指纹一致**——并发与串行拿到同一批产物；
3. **没有容器档执行体时不降级**：返回 `blocked`，不许拿 Job Object 冒充容器。
"""

from __future__ import annotations

from typing import Any

import pytest

from hos_ares_core.parallel import (
    CONTAINER_IMAGE,
    ContainerRun,
    ContainerTask,
    ParallelError,
    run_parallel_containers,
)
from hos_ares_core.sandbox.runner import ExecResult, select_executor

SL = chr(0x2F)
_SHELL = "sh"

#: 每个任务在容器里算一个可复现的数：并行与串行必须给同一个值
_SCRIPT = (
    "import hashlib,sys;"
    "print(hashlib.sha256(sys.argv[1].encode()).hexdigest()[:16])"
)


def _tasks(ids: tuple[str, ...]) -> tuple[ContainerTask, ...]:
    return tuple(
        ContainerTask(task_id=tid, argv=("python", "-c", _SCRIPT, tid)) for tid in ids
    )


def _container_executor() -> Any:
    executor = select_executor()
    if executor is None or getattr(executor, "isolation", "") != "container":
        pytest.skip("本机没有容器档执行体（E5 容器级未验）")
    return executor


# ── 判据 1：真容器 ────────────────────────────────────────────
def test_each_task_runs_in_its_own_container() -> None:
    executor = _container_executor()
    runs = run_parallel_containers(executor, _tasks(("t1", "t2", "t3")))
    assert [r.task_id for r in runs] == ["t1", "t2", "t3"], "必须按 task_id 排序"
    for run in runs:
        assert run.isolation == "container", run.isolation
        assert run.outcome == "ok", (run.task_id, run.reason)


# ── 判据 2：并发 == 串行（同一指纹）────────────────────────────
def test_concurrent_matches_serial() -> None:
    executor = _container_executor()
    ids = ("t3", "t1", "t2")
    parallel = run_parallel_containers(executor, _tasks(ids))
    serial = tuple(
        run_parallel_containers(executor, (task,))[0]
        for task in sorted(_tasks(ids), key=lambda t: t.task_id)
    )
    assert [r.stdout for r in parallel] == [r.stdout for r in serial]


def test_result_shape_is_stable() -> None:
    executor = _container_executor()
    runs = run_parallel_containers(executor, _tasks(("only",)))
    assert len(runs) == 1 and runs[0].task_id == "only"
    assert runs[0].stdout.strip(), "容器必须真的产出东西"


# ── 判据 3：不降级 ────────────────────────────────────────────
def test_non_container_executor_is_blocked_not_downgraded() -> None:
    class _JobLike:
        isolation = "job-object"

        def run(self, *a: Any, **kw: Any) -> ExecResult:  # noqa: ANN401
            raise AssertionError("非容器档绝不允许真的执行")

    runs = run_parallel_containers(_JobLike(), _tasks(("t1",)))
    assert runs[0].outcome == "blocked"
    assert "job-object" in runs[0].reason


def test_limit_is_enforced_like_the_in_process_version() -> None:
    with pytest.raises(ParallelError, match="too_many_tasks"):
        run_parallel_containers(object(), _tasks(("a", "b", "c", "d")))


def test_empty_batch_is_empty_not_error() -> None:
    assert run_parallel_containers(object(), ()) == ()


# ── 隔离边界：容器之间互相看不见 ──────────────────────────────
def test_containers_do_not_share_a_filesystem() -> None:
    """一个容器写的文件，另一个容器读不到——这是"每任务一容器"的意义。"""
    executor = _container_executor()
    writer = ContainerTask(
        task_id="w", argv=(_SHELL, "-c", "echo secret > /tmp/handoff && echo wrote"),
    )
    reader = ContainerTask(task_id="r", argv=(_SHELL, "-c", "cat /tmp/handoff || echo gone"))
    runs = {r.task_id: r for r in run_parallel_containers(executor, (writer, reader))}
    assert "wrote" in runs["w"].stdout
    assert "gone" in runs["r"].stdout, "容器之间共享了文件系统"


def test_image_default_is_the_sandbox_one() -> None:
    """默认镜像与沙箱 attest 同口径，别在两处各写一个。"""
    from hos_ares_core.sandbox.attest import CONTAINER_IMAGE as ATTEST_IMAGE

    assert CONTAINER_IMAGE == ATTEST_IMAGE
