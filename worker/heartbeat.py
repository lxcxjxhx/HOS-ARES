"""心跳线程：每 ``heartbeat_s`` 续租一次（04 §2）。

连续 2 次续租失败 → 抛出 ``LeaseLost``；
若已确认租约丢失还在干活 → 立即自杀式退出，防双跑（03 §3）。
"""

from __future__ import annotations

import threading
from typing import Callable

from veripen_core.lease import Lease, LeaseLost


class Heartbeat(threading.Thread):
    def __init__(
        self,
        lease: Lease,
        task_id: str,
        worker_id: str,
        on_lost: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(name=f"hb-{task_id}", daemon=True)
        self.lease = lease
        self.task_id = task_id
        self.worker_id = worker_id
        self.on_lost = on_lost
        self._stop = threading.Event()
        self.misses = 0
        self.beats = 0

    def run(self) -> None:
        interval = self.lease.cfg.heartbeat_s
        while not self._stop.wait(interval):
            if self.lease.renew(self.task_id, self.worker_id):
                self.beats += 1
                self.misses = 0
                continue
            self.misses += 1
            if self.misses >= 2:
                if self.on_lost is not None:
                    self.on_lost()
                else:  # pragma: no cover - 默认行为即自杀式退出
                    raise LeaseLost(f"租约已丢失：{self.task_id}")
                return

    def stop(self) -> None:
        self._stop.set()
