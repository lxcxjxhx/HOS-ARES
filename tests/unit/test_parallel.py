"""B49 任务级并行（`H02 §6` / `H07` B49）。

判据：3 任务并发与串行 findings 逐条相同 · 调度交错不影响结果 · 跨任务读黑板直接失败 ·
阶段失败回退且预算不重算。

容器级隔离不在本批：结果里 `isolation="process"` 是显式声明。
"""

from __future__ import annotations

import threading
import time

import pytest

from veripen_core.blackboard import Blackboard
from veripen_core.parallel import (
    ISOLATION,
    MAX_PARALLEL,
    ParallelError,
    TaskResult,
    TaskScope,
    merge_sha,
    run_parallel,
    run_stages,
)
from veripen_core.store import Store


def _runner(task_id: str) -> TaskResult:
    """每个任务产出相同形状的发现；睡一下让调度真的交错。"""
    time.sleep(0.02)
    return TaskResult(task_id=task_id, findings=(
        {"oracle": "idor_cross_account", "asset": task_id, "verdict": "VERIFIED"},
        {"oracle": "ssrf_oob", "asset": task_id, "verdict": "BLOCKED"},
    ), steps=2)


def _runners(*ids: str) -> dict[str, object]:
    return {tid: _runner for tid in ids}


# ── A 并发 == 串行 ─────────────────────────────────────────────
def test_parallel_matches_sequential_findings_sha() -> None:
    ids = ("t-1", "t-2", "t-3")
    parallel = run_parallel(_runners(*ids))  # type: ignore[arg-type]
    sequential = [ _runner(tid) for tid in ids ]
    assert [r.task_id for r in parallel] == list(ids), "结果按 task_id 排序"
    assert merge_sha(parallel) == merge_sha(sequential)


def test_limit_is_three_and_hard() -> None:
    assert MAX_PARALLEL == 3
    with pytest.raises(ParallelError) as err:
        run_parallel(_runners("t-1", "t-2", "t-3", "t-4"))  # type: ignore[arg-type]
    assert err.value.code == "too_many_tasks"


def test_input_order_does_not_change_result() -> None:
    a = run_parallel({"t-1": _runner, "t-2": _runner})  # type: ignore[dict-item]
    b = run_parallel({"t-2": _runner, "t-1": _runner})  # type: ignore[dict-item]
    assert merge_sha(a) == merge_sha(b)


def test_single_task_failure_does_not_sink_the_batch() -> None:
    def boom(_tid: str) -> TaskResult:
        raise RuntimeError("db locked")

    results = run_parallel({"t-1": _runner, "t-2": boom})  # type: ignore[dict-item]
    by_id = {r.task_id: r for r in results}
    assert by_id["t-1"].findings and "db locked" in by_id["t-2"].error


def test_results_declare_process_isolation() -> None:
    state = run_stages([("a", lambda: None)])
    assert state.as_dict()["isolation"] == ISOLATION == "process"


# ── B 跨任务读 = 事故 ─────────────────────────────────────────
@pytest.fixture()
def store() -> Store:
    s = Store(":memory:").init()
    yield s
    s.close()


def test_cross_task_read_raises_not_filters(store: Store) -> None:
    bb = Blackboard(store)
    bb.append(task_id="t-1", subject="a", predicate="p", value="v",
              origin="observed", evidence_id="ev-1")
    scope = TaskScope(bb, "t-1")
    assert len(scope.facts()) == 1
    with pytest.raises(ParallelError) as err:
        scope.read("t-2")
    assert err.value.code == "cross_task_read"


def test_task_scope_sees_only_own_facts(store: Store) -> None:
    bb = Blackboard(store)
    for tid in ("t-1", "t-2"):
        bb.append(task_id=tid, subject="s", predicate="p", value=tid,
                  origin="observed", evidence_id=f"ev-{tid}")
    assert {f.value for f in TaskScope(bb, "t-2").facts()} == {"t-2"}


# ── C 阶段推进与回退 ─────────────────────────────────────────
def test_stage_failure_rolls_back_and_keeps_budget() -> None:
    budget: dict[str, int] = {}
    calls: list[str] = []

    def ok(name: str) -> None:
        calls.append(name)

    def bad() -> None:
        raise RuntimeError("stage exploded")

    state = run_stages([("discover", lambda: ok("discover")),
                        ("plan", bad),
                        ("execute", lambda: ok("execute"))], budget=budget)
    assert state.stage == "discover", "失败回退到上一阶段"
    assert state.done == ["discover"]
    assert "stage exploded" in state.failed
    assert calls == ["discover"], "失败后的阶段不许执行"
    assert budget == {"discover": 1}, "预算只记跑过的，不重算"


def test_stage_success_advances() -> None:
    state = run_stages([("a", lambda: None), ("b", lambda: None)])
    assert state.done == ["a", "b"] and state.stage == "b" and not state.failed


# ── D 真并发（不是"看起来并发"）──────────────────────────────
def test_tasks_really_run_concurrently() -> None:
    active = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def runner(task_id: str) -> TaskResult:
        with lock:
            active["now"] += 1
            active["peak"] = max(active["peak"], active["now"])
        time.sleep(0.05)
        with lock:
            active["now"] -= 1
        return TaskResult(task_id=task_id)

    run_parallel({"t-1": runner, "t-2": runner, "t-3": runner})  # type: ignore[dict-item]
    assert active["peak"] >= 2, "峰值并发小于 2 说明根本没并行"
