"""worker 进程（`04 §2`）：心跳线程 + 领取循环 + 执行器。

B2 只交付"领 → 续 → 释放/完成"的骨架，执行器留成可注入的 `executor` 回调。
真正的 oracle 执行在 B5 起接。
"""

from __future__ import annotations

import logging
import os
import signal
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from veripen_core.lease import HeartbeatThread, LeaseHandle, LocalClock, make_worker_id
from veripen_core.models import LEASE_TTL_S, Task, TaskEvent, TaskState
from veripen_core.scheduler import Scheduler
from veripen_core.store import Store, utc_now

log = logging.getLogger("veripen.worker")

#: 执行器签名：拿到 task 返回 (成功?, result dict)
Executor = Callable[[Task], tuple[bool, dict]]


def _noop_executor(task: Task) -> tuple[bool, dict]:
    """默认执行器：不做任何事，直接成功。用于 B2 的调度骨架验证。"""
    return True, {"note": "noop executor", "task_id": task.id}


@dataclass
class Worker:
    """一个 worker 进程的完整生命周期。"""

    store: Store
    worker_id: str = field(default_factory=make_worker_id)
    executor: Executor = _noop_executor
    ttl_s: int = LEASE_TTL_S
    idle_sleep_s: float = 1.0
    clock: LocalClock = field(default_factory=LocalClock)
    _stop: threading.Event = field(default_factory=threading.Event, init=False)
    _current: LeaseHandle | None = field(default=None, init=False)
    processed: int = field(default=0, init=False)

    # ---------- 主循环 ----------

    def run_once(self) -> Task | None:
        """领一个任务并执行完。返回处理过的 task（或 None）。

        这里刻意用 `Scheduler.pick_next_respecting_partition` 而不是裸 `lease_next`，
        因为 `04 §4` 要求同资产串行。
        """
        scheduler = Scheduler(store=self.store, clock=self.clock)
        task = scheduler.pick_next_respecting_partition(self.worker_id, ttl_s=self.ttl_s)
        if task is None:
            return None

        lease = LeaseHandle(
            task_id=task.id, worker_id=self.worker_id, store=self.store, clock=self.clock, ttl_s=self.ttl_s
        )
        self._current = lease
        hb = HeartbeatThread(lease)
        hb.start()

        try:
            # LEASED -> RUNNING
            if not self.store.transition(task.id, TaskState.LEASED, TaskState.RUNNING, now=self.clock.now_s()):
                log.warning("lost_before_run task_id=%s", task.id)
                return None

            ok, result = self.executor(task)
            if lease.lost:
                # 租约丢了：不许写结果，直接放弃（`04 §2` 防双跑）
                log.warning("lease_lost_discard task_id=%s", task.id)
                return task

            now = self.clock.now_s()
            if ok:
                self.store.transition(task.id, TaskState.RUNNING, TaskState.DONE, now=now, result=result)
            else:
                moved = self.store.transition(
                    task.id,
                    TaskState.RUNNING,
                    TaskState.FAILED,
                    now=now,
                    result=result,
                    error_class="retryable",
                )
                if moved:
                    self.store.transition(task.id, TaskState.FAILED, TaskState.QUEUED, now=now)
            self.processed += 1
            self._append_event(task.id, "budget" if ok else "error", ok, {"executor": ok})
            return task
        finally:
            hb.stop()
            self._current = None

    def run_forever(self) -> None:
        """领取循环。空闲 sleep `idle_sleep_s`（`04 §2`）。"""
        while not self._stop.is_set():
            task = self.run_once()
            if task is None:
                self._stop.wait(self.idle_sleep_s)
            if self._current is not None and self._current.lost:
                # 租约丢失 → 自杀式退出（`03 §3` lease_lost_suicide）
                log.error("lease_lost_suicide worker_id=%s", self.worker_id)
                os._exit(3)

    def stop(self) -> None:
        self._stop.set()

    # ---------- 信号处理 ----------

    def install_signal_handlers(self) -> None:
        """SIGTERM → 释放租约而不是等它过期（`04 §2`）。

        释放让重排快 30 秒；不释放则要靠回收扫描，演示看不出差别但生产会慢。
        """

        def _handler(signum, frame):  # noqa: ARG001
            log.info("sigterm_release worker_id=%s", self.worker_id)
            if self._current is not None:
                self._current.release()
            self.stop()

        try:
            signal.signal(signal.SIGTERM, _handler)
            signal.signal(signal.SIGINT, _handler)
        except ValueError:
            # 非主线程无法装信号处理器，测试环境会走到这里
            pass

    # ---------- 内部 ----------

    def _append_event(self, task_id: str, kind: str, ok: bool, detail: dict) -> None:
        try:
            self.store.append_event(
                TaskEvent(task_id=task_id, ts=utc_now(), kind=kind, ok=ok, detail=detail)
            )
        except ValueError:
            log.warning("event_detail_too_large task_id=%s", task_id)


def main() -> int:  # pragma: no cover — 进程入口，不在单测里跑
    import argparse

    p = argparse.ArgumentParser(description="HOS-ARES worker")
    p.add_argument("--db", default="var/veripen.db")
    p.add_argument("--id", default=None)
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    os.makedirs(os.path.dirname(args.db) or ".", exist_ok=True)
    store = Store(args.db)
    w = Worker(store=store, worker_id=args.id or make_worker_id())
    w.install_signal_handlers()
    log.info("worker_start id=%s", w.worker_id)
    w.run_forever()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
