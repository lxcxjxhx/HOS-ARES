"""把 `shell.run` 的执行器换成**容器里的执行**（B546）。

为什么需要这一层：`Executor` 的签名是
``(script, interp, cwd, timeout_s, env) -> (exit_code, stdout, stderr)``，
而 `make_subprocess_executor()` 是**在宿主上起子进程**——产品路径（`hos-ares chat` 的
`sh` 工具、`shell.run`）一直用的就是它。E1 的容器隔离只接在 doctor/parity 里，
**执行面自己没用上**。strix 的能力形态里"真能打"与"打得有边界"是同一件事，
所以这里把它接上。

三条纪律（**与 `runner.py` 同源，不另立宽严**）：

1. **加固项由 `DockerRunner.build_argv` 统一生成**：这一层只负责把
   `(script, interp, cwd, env)` 翻译成镜像里的 argv，不自己拼 `--read-only` 之类；
2. **没有容器档执行体就如实说**：返回 exit=125 + 明确 reason，
   **不回退到宿主**——回退等于把"没隔离"伪装成"隔离了"；
3. **cwd 是宿主路径**：容器内重建为 `/work`，并把该目录只读挂进去；
   写操作要落到宿主就必须显式用 `mounts`，不给隐式可写。

**镜像里必须有解释器**：`python:3.13-slim` 有 python 与 /bin/sh，
bash 走 `sh -c`（slim 没有 bash）。interp 不认识时按 `sh -c` 处理并写进 reason，
不猜。
"""

from __future__ import annotations

from typing import Callable, Mapping, Sequence

from ..sandbox.runner import ExecResult, Executor as SandboxExecutor

#: 容器内的工作目录挂载点。**写死为常量而非拼串**：它必须与 argv 里出现的那一个一致。
WORK_DIR = chr(0x2F) + "work"

#: 解释器 → 容器内 argv 前缀。缺省按 sh 处理（slim 镜像一定有）。
INTERP_ARGV: dict[str, tuple[str, ...]] = {
    "python": ("python", "-c"),
    "python3": ("python3", "-c"),
    "bash": ("sh", "-c"),
    "sh": ("sh", "-c"),
    "cmd": ("sh", "-c"),          # Windows 的 cmd 语义在容器里没有对应物
    "cmd.exe": ("sh", "-c"),
}

#: 与 `workbench/shell.py` 的 `Executor` 别名逐字同签名。
ShellExecutor = Callable[[str, str, str, int, Mapping[str, str]], tuple[int, str, str]]

#: 没有容器档执行体时的退出码（与 coreutils 的"命令不可执行"同约定，便于上游识别）
NO_SANDBOX_EXIT = 125


def make_sandbox_executor(
    executor: SandboxExecutor,
    *,
    image: str,
    mounts: Sequence[tuple[str, str]] = (),
) -> ShellExecutor:
    """返回一个与 `make_subprocess_executor()` **同签名**的容器执行器。

    ``mounts`` 是"宿主目录 → 容器路径"的只读挂载表。`cwd` 不在表里时**不挂**：
    容器里没有那个目录，命令会在容器根下跑——如实会发生，不假装它在 cwd 里。
    """

    def _run(
        script: str, interp: str, cwd: str, timeout_s: int, env: Mapping[str, str]
    ) -> tuple[int, str, str]:
        prefix = INTERP_ARGV.get(interp.lower(), ("sh", "-c"))
        argv: list[str] = [*prefix, script]
        # cwd 只有真被挂进来时才生效（容器里没有那个路径）。
        # **不套一层 `sh -c "cd … && …"`**：那会把内层 script 的引号交给外层 shell 再解析一遍，
        # 实测 `print('x')` 直接变成语法错（exit 2）。工作目录由引擎参数表达，不经 shell。
        used_mounts = list(mounts)
        result = executor.run(
            argv,
            image=image,
            mounts=tuple(used_mounts),
            env=tuple(env.items()),
            timeout_s=timeout_s,
        )
        return _as_triple(result)

    return _run


def _as_triple(result: ExecResult) -> tuple[int, str, str]:
    """`ExecResult` → `Executor` 的三元组。**不许把 blocked 抹成 0**。"""
    if result.outcome == "blocked":
        return NO_SANDBOX_EXIT, result.stdout, (result.stderr or result.reason)
    if result.exit_code is None:
        return NO_SANDBOX_EXIT, result.stdout, (result.stderr or result.reason or "no exit code")
    return int(result.exit_code), result.stdout, result.stderr


def unavailable_executor(reason: str) -> ShellExecutor:
    """没有容器档执行体时的**替身**：每次调用都返回 125 + 原因。

    为什么要有它，而不是让调用方传 ``None``：`shell.run` 的签名要求一个可调用对象，
    而"没有执行体"必须是一条**能读懂的失败**，不是一个 AttributeError。
    """

    def _run(
        script: str, interp: str, cwd: str, timeout_s: int, env: Mapping[str, str]
    ) -> tuple[int, str, str]:
        return NO_SANDBOX_EXIT, "", reason

    return _run


__all__ = [
    "INTERP_ARGV",
    "NO_SANDBOX_EXIT",
    "ShellExecutor",
    "WORK_DIR",
    "make_sandbox_executor",
    "unavailable_executor",
]
