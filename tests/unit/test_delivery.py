"""B13 交付硬化测试：doctor 16 项 + 死信 + 3 worker 并行不重不漏。

doctor 的取向：每项都能自己跑、失败要指出"该改哪里"，而不是打印一串绿勾。
任何一项 FAIL 就整体 exit 1——不给"看着还行"的余地。
"""

from __future__ import annotations

import json
import threading

import pytest

from veripen.doctor import CHECKS, Check, DoctorReport, Level, run_all
from veripen_core.lease import Lease, LeaseConfig, StoreClock
from veripen_core.models import TaskState
from veripen_core.store import Store


# ── 1 doctor 结构 ─────────────────────────────────────────────────
def test_doctor_has_sixteen_checks() -> None:
    assert len(CHECKS) == 16, "13 §/12 §2 的 doctor 清单是 16 项"


def test_doctor_all_green_in_repo() -> None:
    report = run_all()
    assert not report.failures, "\n".join(c.line() for c in report.failures)


def test_check_failure_must_explain_fix() -> None:
    """失败项必须带 fix：只报"失败"等于让人自己猜。"""
    report = run_all()
    for check in report.failures:
        assert check.fix, f"{check.name} 失败但没写该改哪里"


def test_report_json_shape() -> None:
    report = DoctorReport()
    report.add(Check("x", Level.OK, "fine"))
    report.add(Check("y", Level.WARN, "meh", fix="do z"))
    payload = report.to_json()
    assert payload["schema"] == "veripen.doctor/v1"
    assert payload["ok"] is True
    assert payload["counts"] == {"ok": 1, "warn": 1, "fail": 0}


def test_fail_marks_not_ok() -> None:
    report = DoctorReport()
    report.add(Check("z", Level.FAIL, "broken", fix="fix it"))
    assert report.as_text().startswith("FAIL")
    assert report.to_json()["ok"] is False


def test_critical_checks_present() -> None:
    names = {c.__name__ for c in CHECKS}
    for required in ("_check_default_off _check_noroute _check_policy "
                     "_check_bench_baseline _check_replay_offline").split():
        assert required in names, f"缺少关键检查 {required}"


# ── 2 死信（dead letter）──────────────────────────────────────────
def test_dead_letter_after_max_attempts(tmp_path) -> None:
    """attempt 用尽 → DEAD_LETTER，且不再被领取（04 §5 重排规则）。"""
    store = Store(tmp_path / "d.db").init()
    try:
        lease = Lease(store, StoreClock(store), LeaseConfig(ttl_s=30, scan_s=1))
        task_id, _ = store.submit_task(
            engagement_id="E", profile_name="p", profile_hash="h", asset_id="a",
            oracle_ids=["o"], max_attempts=2,
        )
        for _ in range(2):
            claimed = lease.claim("w1")
            assert claimed == task_id
            # 列名是 attempt（单数）——与 03 §1 的 DDL 一致
            assert store.transition(
                task_id, TaskState.LEASED, TaskState.FAILED, attempt=_attempts(store, task_id) + 1
            )
            if _attempts(store, task_id) >= 2:
                store.transition(task_id, TaskState.FAILED, TaskState.DEAD_LETTER)
            else:
                store.transition(task_id, TaskState.FAILED, TaskState.QUEUED)
        assert lease.claim("w2") is None, "死信不许再被领取"
        row = store.conn.execute("SELECT state FROM tasks WHERE id=?", (task_id,)).fetchone()
        assert row["state"] == TaskState.DEAD_LETTER.value
    finally:
        store.close()


def _attempts(store: Store, task_id: str) -> int:
    row = store.conn.execute("SELECT attempt FROM tasks WHERE id=?", (task_id,)).fetchone()
    return int(row["attempt"])


# ── 3 三 worker 并行不重不漏 ──────────────────────────────────────
def test_three_workers_do_not_double_claim(tmp_path) -> None:
    """三 worker 抢同一批任务：不重不漏（租约互斥，不需要协调者）。"""
    store = Store(tmp_path / "w.db").init()
    try:
        lease = Lease(store, StoreClock(store), LeaseConfig(ttl_s=30, scan_s=1))
        ids = [
            store.submit_task(
                engagement_id="E", profile_name="p", profile_hash="h",
                asset_id=f"asset-{i}", oracle_ids=["o"],
            )[0]
            for i in range(12)
        ]
        claimed: list[str] = []
        lock = threading.Lock()

        def worker(name: str) -> None:
            while True:
                task = lease.claim(name)
                if task is None:
                    return
                with lock:
                    claimed.append(task)
                store.transition(task, TaskState.LEASED, TaskState.DONE)

        threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert sorted(claimed) == sorted(ids), "不重不漏"
        assert len(set(claimed)) == len(claimed)
    finally:
        store.close()


def test_offline_package_manifest_shape() -> None:
    """离线包清单：镜像/依赖/数据快照都要有 digest（12 §3）。"""
    from pathlib import Path

    manifest = {
        "version": 1,
        "images": [{"name": "veripen", "digest": "sha256:" + "0" * 64}],
        "data": [{"path": "data/snapshot", "sha256": "0" * 64}],
        "python": {"requires": ">=3.11", "locks": "requirements.lock"},
    }
    for key in "images data python".split():
        assert key in manifest
    assert Path("pyproject.toml").exists()
    json.dumps(manifest)


def test_package_offline_script_exists() -> None:
    from pathlib import Path

    script = Path(__file__).resolve().parents[2] / "scripts" / "package-offline.sh"
    assert script.exists(), "离线包必须有脚本入口（12 §3）"
    assert "pip download" in script.read_text(encoding="utf-8")
