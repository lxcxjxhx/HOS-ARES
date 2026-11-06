"""Windows Job Object 受限执行（`H26 B84`）。

**它不是容器。** 保证三件事：

1. 进程数上限（防 fork 炸弹）；
2. 内存上限（防 OOM 拖垮宿主）；
3. 句柄关闭 / 超时即整组杀（无残留）。

不做：文件系统命名空间、网络隔离、权限降级。
所以 ``isolation`` 必须如实写 ``job-object``，**不许冒充 docker**——
那会让 E1 的"真隔离未验"变成假绿。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Sequence

# ── Win32 常量 ────────────────────────────────────────────────────
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wt.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wt.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wt.DWORD),
        ("SchedulingClass", wt.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if sys.platform == "win32" else None


def available() -> bool:
    """Job Object 可用性：Windows + kernel32 关键符号在位。"""
    if sys.platform != "win32" or kernel32 is None:
        return False
    for name in ("CreateJobObjectW", "SetInformationJobObject",
                 "AssignProcessToJobObject", "TerminateJobObject"):
        if not hasattr(kernel32, name):
            return False
    return True


@dataclass(frozen=True)
class JobLimits:
    active_processes: int = 32
    job_memory_bytes: int = 512 * 1024 * 1024
    process_memory_bytes: int = 256 * 1024 * 1024


@dataclass(frozen=True)
class JobExecResult:
    exit_code: int
    stdout: bytes
    stderr: bytes
    outcome: str
    reason: str = ""
    isolation: str = "job-object"


class WinJobRunner:
    """在 Job Object 里跑一条命令。``isolation`` 恒为 ``job-object``。"""

    name = "job-object"

    def __init__(self, limits: JobLimits | None = None) -> None:
        self.limits = limits or JobLimits()

    def available(self) -> bool:
        return available()

    # ── Job 生命周期 ────────────────────────────────────────────
    def _create_job(self) -> int:
        assert kernel32 is not None
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            raise OSError(ctypes.get_last_error(), "CreateJobObject 失败")
        info = _ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = (
            JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            | JOB_OBJECT_LIMIT_JOB_MEMORY
            | JOB_OBJECT_LIMIT_PROCESS_MEMORY
            | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        info.BasicLimitInformation.ActiveProcessLimit = self.limits.active_processes
        info.JobMemoryLimit = self.limits.job_memory_bytes
        info.ProcessMemoryLimit = self.limits.process_memory_bytes
        ok = kernel32.SetInformationJobObject(
            job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info), ctypes.sizeof(info),
        )
        if not ok:
            kernel32.CloseHandle(job)
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject 失败")
        return int(job)

    @staticmethod
    def _close(job: int) -> None:
        if kernel32 is not None and job:
            # KILL_ON_JOB_CLOSE：关句柄就把整组杀掉，这是"无残留"的实现方式
            kernel32.CloseHandle(job)

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout_s: int = 120,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> JobExecResult:
        if not self.available():
            return JobExecResult(1, b"", b"", "blocked", "job_object_unavailable")

        job = self._create_job()
        proc: subprocess.Popen[bytes] | None = None
        try:
            flags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
            proc = subprocess.Popen(
                list(argv), cwd=cwd, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=flags,
            )
            assert kernel32 is not None
            # AssignProcessToJobObject 要 PROCESS_SET_QUOTA | PROCESS_TERMINATE。
            # Popen 的 _handle 是 CreateProcess 返回的进程句柄，权限够；优先用它，
            # 拿不到再 OpenProcess（**不要**写成 SET_INFORMATION|QUERY_INFORMATION，
            # 那两个不是 Assign 要的权限，会静默失败）。
            handle = int(getattr(proc, "_handle", 0) or 0)
            if not handle:
                handle = int(kernel32.OpenProcess(0x0100 | 0x0001, False, proc.pid) or 0)
            assigned = False
            if handle:
                assigned = bool(kernel32.AssignProcessToJobObject(job, wt.HANDLE(handle)))
            if not assigned:
                # 没进 job 就别假装受限：明确报出来，由调用方决定
                last_error = ctypes.get_last_error()
                out, errb = proc.communicate(timeout=timeout_s)
                return JobExecResult(
                    proc.returncode or 1, out or b"", errb or b"",
                    "error", f"assign_failed:err={last_error}",
                )
            try:
                out, err = proc.communicate(timeout=timeout_s)
                return JobExecResult(proc.returncode or 0, out or b"", err or b"",
                                     "ok" if proc.returncode == 0 else "error",
                                     "" if proc.returncode == 0 else f"exit={proc.returncode}")
            except subprocess.TimeoutExpired:
                # 超时：先杀整组（子进程一起），再收尸
                kernel32.TerminateJobObject(wt.HANDLE(job), 1)
                try:
                    out, err = proc.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    out, err = b"", b""
                return JobExecResult(124, out or b"", err or b"", "error",
                                     f"timeout>{timeout_s}s")
        finally:
            self._close(job)


__all__ = ["JobExecResult", "JobLimits", "WinJobRunner", "available"]
