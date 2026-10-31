"""worker 进程：领取 → 带心跳执行 → SIGTERM 释放租约（04 §2）。

启动：``python -m worker.worker``（库路径读 ``VERIPEN_DB``）。
``VERIPEN_WORKER_HOLD_S`` 仅用于演示/测试：执行体睡这么久，模拟长任务。
"""

from __future__ import annotations

import os
import signal
import sys
import time
import uuid

from veripen_core.lease import Lease, LeaseConfig, LeaseLost, StoreClock
from veripen_core.scheduler import Scheduler
from veripen_core.store import Store

from .heartbeat import Heartbeat

DEFAULT_CONFIG = LeaseConfig()


def make_worker_id() -> str:
    return f"{os.uname().nodename if hasattr(os, 'uname') else os.environ.get('COMPUTERNAME', 'host')}"\
           f"-{os.getpid()}-{uuid.uuid4().hex[:8]}"


class Worker:
    def __init__(
        self,
        store: Store,
        worker_id: str,
        cfg: LeaseConfig | None = None,
        hold_s: float = 0.0,
    ) -> None:
        self.store = store
        self.worker_id = worker_id
        self.cfg = cfg or DEFAULT_CONFIG
        self.lease = Lease(store, StoreClock(store), self.cfg)
        self.hold_s = hold_s
        self.stopping = False
        self.current: str | None = None
        self.scheduler = Scheduler(store, self.lease, self.execute)

    # ── 执行体 ──────────────────────────────────────────────────
    def execute(self, task_id: str) -> None:
        self.current = task_id
        hb = Heartbeat(self.lease, task_id, self.worker_id, on_lost=self._on_lease_lost)
        hb.start()
        try:
            deadline = time.monotonic() + self.hold_s
            while time.monotonic() < deadline:
                if self.stopping:
                    break
                time.sleep(0.02)
            if hb.misses >= 2:
                raise LeaseLost(f"租约丢失：{task_id}")
        finally:
            hb.stop()
            hb.join(timeout=1)
            self.current = None

    def _on_lease_lost(self) -> None:
        # 还在跑就自杀式退出：宁可丢一次结果，也不允许双跑（03 §3）
        sys.stderr.write(f"lease_lost_suicide worker={self.worker_id}\n")
        sys.stderr.flush()
        os._exit(3)

    # ── 生命周期 ────────────────────────────────────────────────
    def run_once(self) -> bool:
        report = self.scheduler.tick(self.worker_id)
        return report.claimed is not None

    def run_until_idle(self) -> int:
        n = 0
        while self.run_once():
            n += 1
        return n

    def run_forever(self) -> None:
        while not self.stopping:
            if not self.run_once():
                time.sleep(0.05)

    def shutdown(self) -> None:
        """SIGTERM 路径：释放租约（state 回 QUEUED，attempt 不 +1）。"""
        self.stopping = True
        if self.current is not None:
            self.lease.release(self.current, self.worker_id)


def main() -> int:
    store = Store(os.environ.get("VERIPEN_DB", "out/veripen.db")).init()
    cfg = LeaseConfig(
        heartbeat_s=int(os.environ.get("VERIPEN_HEARTBEAT_S", DEFAULT_CONFIG.heartbeat_s)),
        ttl_s=int(os.environ.get("VERIPEN_LEASE_TTL_S", DEFAULT_CONFIG.ttl_s)),
        scan_s=int(os.environ.get("VERIPEN_SCAN_S", DEFAULT_CONFIG.scan_s)),
    )
    worker = Worker(
        store,
        make_worker_id(),
        cfg=cfg,
        hold_s=float(os.environ.get("VERIPEN_WORKER_HOLD_S", "0")),
    )

    def _term(_signum: int, _frame: object) -> None:
        worker.shutdown()
        store.close()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)
    worker.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
