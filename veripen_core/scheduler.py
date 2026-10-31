"""调度器：回收过期租约 + 领取 + 执行一次（04 §2）。

刻意很薄：调度器**不执行任务**、不发请求（01 §4 职责表）。
它只做三件事：回收、领取、把任务交给 handler。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from .lease import Lease
from .models import TaskState
from .store import Store


@dataclass
class TickReport:
    reaped: list[tuple[str, str]] = field(default_factory=list)
    claimed: str | None = None
    handled: bool = False
    error: str | None = None


class Scheduler:
    """单 worker 的调度循环；多 worker 靠租约互斥，不需要协调者。"""

    def __init__(
        self,
        store: Store,
        lease: Lease,
        handler: Callable[[str], None] | None = None,
    ) -> None:
        self.store = store
        self.lease = lease
        self.handler = handler

    def tick(self, worker_id: str) -> TickReport:
        report = TickReport(reaped=self.lease.reap_expired())
        task_id = self.lease.claim(worker_id)
        report.claimed = task_id
        if task_id is None or self.handler is None:
            return report
        if not self.store.transition(task_id, TaskState.LEASED, TaskState.RUNNING):
            report.error = "lease_lost_before_run"
            return report
        try:
            self.handler(task_id)
            report.handled = True
        except Exception as exc:  # noqa: BLE001 - 失败分类在 04 §3，此处只记录
            report.error = f"{type(exc).__name__}: {exc}"
        return report

    def run_forever(self, worker_id: str, stop: Callable[[], bool]) -> None:
        interval = self.lease.cfg.scan_s
        while not stop():
            report = self.tick(worker_id)
            if report.claimed is None:
                time.sleep(min(1, interval))
