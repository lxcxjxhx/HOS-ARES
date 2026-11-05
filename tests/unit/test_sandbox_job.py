"""B84 · Windows 受限执行（H26 判据 B–E）。

只测 Windows Job Object 这一档；docker 分支不在本机可测范围。
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from veripen_core.sandbox import win_job
from veripen_core.sandbox.runner import JobObjectExecutor, select_executor

SL = chr(0x2F)
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Job Object 仅 Windows")


def _runner(active: int = 32):
    return JobObjectExecutor(win_job.WinJobRunner(win_job.JobLimits(active_processes=active)))


# ── B：如实标 job-object ──────────────────────────────────────
def test_isolation_is_labelled() -> None:
    r = _runner().run([sys.executable, "-c", "print(1)"], timeout_s=30)
    assert r.isolation == "job-object", "不许冒充 container"
    assert r.outcome == "ok" and r.stdout.strip() == "1"


def test_select_executor_reports_its_name() -> None:
    ex = select_executor()
    assert ex is not None, "本机无 docker 但有 Job Object，不该是 None"
    assert ex.name in ("docker", "job-object")


# ── D：超时整组杀 ────────────────────────────────────────────
def test_timeout_kills_group() -> None:
    started = time.time()
    r = _runner().run(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        timeout_s=2,
    )
    elapsed = time.time() - started
    assert r.exit_code == 124, f"超时应返回 124，实际 {r.exit_code}"
    assert "timeout" in r.reason
    assert elapsed < 20, f"超时后没及时返回：{elapsed:.1f}s"


def test_no_orphan_after_timeout() -> None:
    """超时后整组回收：不应留下同名 python 进程在跑 sleep(77)。"""
    marker = "job-object-orphan-probe-77"
    script = f"import time; print('{marker}', flush=True); time.sleep(60)"
    _runner().run([sys.executable, "-c", script], timeout_s=2)
    time.sleep(1)
    out = subprocess.run(
        ["tasklist", SL + "fo", "csv", SL + "v"], capture_output=True, text=True, errors="replace"
    ).stdout
    assert marker not in out, "超时后仍有残留进程"


# ── C：进程数上限 ────────────────────────────────────────────
def test_active_process_limit_enforced() -> None:
    """上限 4：起 8 个并发子进程，至少一个应被拒。"""
    script = (
        "import subprocess, sys, time\n"
        "ok = 0\n"
        "for _ in range(8):\n"
        "    p = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(1.5)'])\n"
        "    ok += 1\n"
        "    time.sleep(0.05)\n"
        "print('spawned', ok)\n"
        "time.sleep(2)\n"
    )
    r = _runner(active=4).run([sys.executable, "-c", script], timeout_s=30)
    # 进程数超限时，Popen 会抛 OSError（被截断在 stderr），或整个脚本非 0 退出
    combined = (r.stdout + r.stderr).lower()
    assert r.exit_code != 0 or "error" in combined or "no more" in combined, (
        f"进程数上限没生效：exit={r.exit_code} out={combined[:200]}"
    )


# ── 反例：拿不到 job 就不假装受限 ────────────────────────────
def test_assign_failure_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """AssignProcessToJobObject 失败时必须如实报 error，而不是当成功。"""
    runner = win_job.WinJobRunner()

    class _FakeKernel:
        def CreateJobObjectW(self, *_a: object) -> int:
            return 1

        def SetInformationJobObject(self, *_a: object) -> int:
            return 1

        def AssignProcessToJobObject(self, *_a: object) -> int:
            return 0                     # 失败

        def TerminateJobObject(self, *_a: object) -> int:
            return 1

        def CloseHandle(self, *_a: object) -> int:
            return 1

        def OpenProcess(self, *_a: object) -> int:
            return 0

    monkeypatch.setattr(win_job, "kernel32", _FakeKernel())
    r = runner.run([sys.executable, "-c", "print(1)"], timeout_s=10)
    assert r.outcome == "error" and "assign_failed" in r.reason
