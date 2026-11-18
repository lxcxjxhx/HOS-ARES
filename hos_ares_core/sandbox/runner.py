"""容器执行器（B45）。

**只做两件事**：拼一条加固过的 `docker run` 命令行；把结果归一成 `ExecResult`。
不查库、不解析地址、不判定——地址由调用方在运行期注入（`H02 §2`）。
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence


def _text(value: bytes | str | None) -> str:
    """超时分支里 subprocess 可能给 bytes：统一成 str，别把类型漏进结果。"""
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


class RuntimeUnavailable(RuntimeError):
    """没有可用容器运行时。**必须冒泡成 BLOCKED**，不许在内部吞掉。"""


@dataclass(frozen=True)
class ExecResult:
    """一次沙箱执行的结果。`outcome` 只有三种，没有"部分成功"。"""

    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    artifacts: tuple[str, ...] = ()
    outcome: str = "ok"          # ok | blocked | error
    reason: str = ""
    argv: tuple[str, ...] = field(default=(), repr=False)
    #: 执行档位：``container``（docker/podman）或 ``job-object``（Windows 受限执行）。
    #: **不许把 job-object 写成 container**——那会让 E1 的"真隔离未验"变成假绿。
    isolation: str = "container"


class Executor(Protocol):
    name: str

    def available(self) -> bool: ...

    def run(self, argv: Sequence[str], *, image: str, mounts: Sequence[tuple[str, str]] = (),
            env: Sequence[tuple[str, str]] = (), timeout_s: int = 120) -> ExecResult: ...


def _docker_bin() -> str | None:
    for candidate in ("docker", "podman"):
        found = shutil.which(candidate)
        if found:
            return found
    return None


class DockerRunner:
    """`docker run` 的最保守形态：只读根、无网、无特权、有资源上限、退出即删。"""

    name = "docker"

    def __init__(self, binary: str | None = None) -> None:
        self._binary = binary or _docker_bin()

    def available(self) -> bool:
        return self._binary is not None

    def build_argv(
        self,
        argv: Sequence[str],
        *,
        image: str,
        mounts: Sequence[tuple[str, str]] = (),
        env: Sequence[tuple[str, str]] = (),
        network: str = "none",
        cpus: str = "1",
        memory: str = "1g",
        pids: int = 256,
        allow_unscoped_network: bool = False,
    ) -> list[str]:
        """拼命令行。**加固项不是可选项**，网络放开必须显式同意。"""
        if not self.available():
            raise RuntimeUnavailable("没有 docker/podman：执行必须走 BLOCKED，不许回退宿主")
        if network not in ("none", "bridge"):
            raise ValueError(f"未知网络策略 {network!r}（只支持 none / bridge）")
        if network == "bridge" and not allow_unscoped_network:
            raise ValueError("bridge 等于无限制出网：必须显式 allow_unscoped_network=True")

        cmd = [self._binary or "docker", "run", "--rm",
               "--read-only", "--cap-drop", "ALL",
               "--security-opt", "no-new-privileges",
               "--user", "65534:65534",
               "--network", network,
               "--cpus", cpus, "--memory", memory, "--pids-limit", str(pids)]
        for host_path, container_path in mounts:
            cmd += ["--mount", f"type=bind,src={host_path},dst={container_path},readonly"]
        for key, value in env:
            cmd += ["-e", f"{key}={value}"]
        cmd.append(image)
        cmd.extend(argv)
        return cmd

    def run(
        self,
        argv: Sequence[str],
        *,
        image: str,
        mounts: Sequence[tuple[str, str]] = (),
        env: Sequence[tuple[str, str]] = (),
        timeout_s: int = 120,
        **kwargs: object,
    ) -> ExecResult:
        cmd = self.build_argv(argv, image=image, mounts=mounts, env=env, **kwargs)  # type: ignore[arg-type]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s,
                          encoding="utf-8", errors="replace",
                                  check=False)
        except FileNotFoundError as exc:          # 二进制在、但执行失败
            return ExecResult(None, outcome="blocked", reason=f"runtime_unavailable: {exc}")
        except subprocess.TimeoutExpired as exc:
            return ExecResult(None, stdout=_text(exc.stdout), stderr=_text(exc.stderr),
                              outcome="error", reason=f"timeout>{timeout_s}s")
        return ExecResult(proc.returncode, proc.stdout, proc.stderr,
                          outcome="ok" if proc.returncode == 0 else "error",
                          reason="" if proc.returncode == 0 else f"exit={proc.returncode}")


class JobObjectExecutor:
    """把 Windows Job Object 适配成 ``Executor`` 协议（`H26 B84`）。

    ``image`` / ``mounts`` 在这里**没有意义**：Job Object 不建文件系统命名空间。
    它们被接受但忽略，且 ``isolation`` 如实写 ``job-object``。
    """

    name = "job-object"

    def __init__(self, runner: Any | None = None) -> None:
        from .win_job import WinJobRunner

        self._runner = runner or WinJobRunner()

    def available(self) -> bool:
        return bool(self._runner.available())

    def run(
        self,
        argv: Sequence[str],
        *,
        image: str = "",
        mounts: Sequence[tuple[str, str]] = (),
        env: Sequence[tuple[str, str]] = (),
        timeout_s: int = 120,
    ) -> ExecResult:
        res = self._runner.run(
            list(argv), timeout_s=timeout_s,
            env={k: v for k, v in env} if env else None,
        )
        return ExecResult(
            exit_code=res.exit_code,
            stdout=res.stdout.decode("utf-8", "replace"),
            stderr=res.stderr.decode("utf-8", "replace"),
            outcome=res.outcome,
            reason=res.reason,
            argv=tuple(argv),
            isolation="job-object",
        )


def select_executor() -> Executor | None:
    """三级：docker/podman → Windows Job Object → None（调用方翻 BLOCKED）。

    **不许降级到裸跑**：没有隔离时宁可不执行，也不假装执行过。
    """
    runner = DockerRunner()
    if runner.available():
        return runner
    try:
        job = JobObjectExecutor()
        if job.available():
            return job
    except Exception:  # noqa: BLE001 —— 平台不支持时静默跳过，不打断选择
        pass
    return None
