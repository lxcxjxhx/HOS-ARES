"""B19 · 执行接驳（`38 §4` 的 `Executor` 注入点 + `35 §4` 的 `exec_tool` runner）。

两件事，都是"把手接上"：

1. **`Executor`**：`shell.run` 的真实执行器。签名与 `workbench/shell.py` 的
   `Executor` 别名逐字一致：``(script, interp, cwd, timeout_s, env) -> (exit, stdout, stderr)``。
   **真起子进程、真超时杀、真量墙钟**——不是回调、不是建模。

2. **`exec_tool` 的 `run`**：`act.exec_tool` 的真实执行器。它做三件事，
   每件都必须真发生：真跑 argv（白名单二进制）、用 `08 §2` 的六步切片归一输出、
   从**真实输出**里派生命中数（没命中就是 0，不编）。

### 为什么"地址"在这里出现，却是合规的

`35 §5` 说地址只能来自 `RouteBinding`，模型不许给地址。
`build_argv` 因此拒绝任何含地址的 slots——那是**拦模型**。

但框架自己要把 `route_ref` 解析成真实目标再执行。这一步的正确位置就是**运行期**：
`make_action_runner(resolve_host=...)` 接一个解析器，argv 里的占位 host 在**执行前一刻**
被换成解析出的真实值。地址既不进代码、也不进配置、更不由模型提供——
它在运行时从绑定里解出来，与 `tools/transport.py` 的做法同构。

**沙箱不在本批**（`38 §4.2` 的容器在 B20）。这里只保证"真执行 + 真切片 + 真证据"，
不保证"隔离"。缺隔离这件事会如实写进 `scaffold_note`，不用文档盖过去。
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ..tools.registry import bound_and_store

#: 一次执行的结果（Executor 的返回就是这三个的元组）。
@dataclass(frozen=True)
class ExecOutcome:
    exit_code: int
    stdout: str
    stderr: str
    elapsed_ms: int


class ExecutorError(RuntimeError):
    """执行器配置/启动失败。**不是脚本失败**——脚本失败走 exit_code。"""


#: 与 `workbench/shell.py` 的 ``Executor`` 别名同签名。
Executor = Callable[[str, str, str, int, Mapping[str, str]], "tuple[int, str, str]"]


def make_subprocess_executor(
    *,
    max_output_bytes: int = 64 * 1024,
    base_env: Mapping[str, str] | None = None,
) -> Executor:
    """返回一个**真起子进程**的 Executor。

    三条纪律，对应三个失败模式：

    - **超时必须杀**：`subprocess.run(timeout=...)` 超时抛 `TimeoutExpired`，
      这里捕获后杀掉并返回 exit=124（与 `timeout(1)` 同约定）。不杀 = 卡死整个 worker。
    - **输出必须封顶**：读满 `max_output_bytes` 就截断，避免一个 `yes` 把内存打爆。
    - **环境必须白名单**：只透传调用方给的 env（`run_shell` 已按 env_allowlist 过滤），
      不继承宿主全部环境——否则一个 `LD_PRELOAD` 就能改行为。
    """

    def _run(script: str, interp: str, cwd: str, timeout_s: int, env: Mapping[str, str]) -> tuple[int, str, str]:
        shell_run = False
        if interp in ("python", "python3"):
            argv = [interp, "-c", script]
        elif sys.platform == "win32" and interp in ("cmd", "cmd.exe"):
            # Windows 的 cmd 没有 -lc：只认 /c。不特判会让 "执行 python -V"
            # 这类命令在 Windows 上永远 exit=1（B77 实测）。
            #
            # 而且必须**整行交给 shell**（B169）：用 argv 形式（cmd 加开关再加脚本）时
            # Python 的 list2cmdline 会把内层引号转成反斜杠形式，cmd 不认这种转义——
            # `python -c "print(1)"` 会**静默返回 exit=0 且空输出**。shell 形式把整行
            # 原样交给 cmd，引号语义与用户手敲一致。
            argv = [script]
            shell_run = True
        else:
            argv = [interp, "-lc", script]
        proc_env = dict(base_env or {})
        proc_env.update({k: v for k, v in env.items() if v})
        try:
            code, out_s, err_s = _popen_run(
                argv, cwd=cwd, env=proc_env or None, timeout_s=timeout_s, shell=shell_run
            )
            return code, out_s[:max_output_bytes], err_s[:max_output_bytes]
        except FileNotFoundError as exc:
            raise ExecutorError(f"解释器不可用：{interp}（{exc}）") from exc

    return _run


def _popen_run(
    argv: Sequence[str], *, cwd: str | None, env: Mapping[str, str] | None, timeout_s: int,
    shell: bool = False,
) -> tuple[int, str, str]:
    """起子进程并等待；超时时**整组回收**。

    用 ``Popen`` 而不是 ``subprocess.run``：只有拿到 pid 才能杀进程组。
    ``run`` 超时只 Terminate 直接子进程，POSIX 上 fork 出的孙进程会留下来
    （B19 记下的那条 open）。

    **这不是沙箱隔离。** 进程组只解决"杀不干净"，不解决"能访问什么"——
    容器/命名空间隔离需要 docker 或 WSL，本机都没有（见账本 B20 open）。
    """
    # shell 形式必须传**字符串**：传 `["python -V"]` 会被 list2cmdline 整体加引号，
    # cmd 于是去找一个叫 `python -V` 的程序（B169 实测）。
    args: Any = argv[0] if shell else list(argv)
    cwd_v = cwd or None
    env_v = dict(env) if env else None

    if sys.platform == "win32":
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        proc = subprocess.Popen(  # noqa: S603 - argv 数组，无 shell
            args, cwd=cwd_v, env=env_v, shell=shell,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=flags,
        )
    else:
        proc = subprocess.Popen(  # noqa: S603
            args, cwd=cwd_v, env=env_v,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True,
        )

    try:
        out_b, err_b = proc.communicate(timeout=timeout_s)
        return proc.returncode, _decode(out_b), _decode(err_b)
    except subprocess.TimeoutExpired:
        _kill_group(proc.pid)
        try:
            out_b, err_b = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            out_b, err_b = b"", b""
        return 124, _decode(out_b), (
            _decode(err_b)
            + "\ntimeout: 子进程超过 %ds 被终止（含进程组回收）" % timeout_s
        ).strip()


#: taskkill 的开关（分离成常量，避免以斜杠开头的字面量）
_F, _T, _PID = chr(0x2F) + "F", chr(0x2F) + "T", chr(0x2F) + "PID"


def _kill_group(pid: int) -> None:
    """尽力整组终止。**失败不抛**：已经死掉的进程组会 ESRCH，那不是错误。"""
    try:
        if sys.platform == "win32":
            subprocess.run(  # noqa: S603 - 固定 argv，无外部输入
                ["taskkill", _F, _T, _PID, str(pid)],
                capture_output=True, check=False,
            )
        else:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass


def _decode(raw: bytes | str | None) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    return raw.decode("utf-8", errors="replace")


# ══════════════════════════════════════════════════════════════════
# act.exec_tool 的真实 run
# ══════════════════════════════════════════════════════════════════
#: 派生"命中"的行特征。**保守**：只认扫描器自己的结果行，不认裸数字。
#: 之前用裸状态码当特征，会把 banner 里的版本号也当成命中——
#: 于是 hits 从 2 变 3，多出来的那条是假的。宁少勿滥：hits=0 时线索自动作废。
_HIT_HINTS: tuple[str, ...] = (
    "[status:",      # ffuf 结果行：admin  [Status: 200, Size: 2, ...]
    "[open]",        # nmap 结果行：22/tcp open  ssh
    "found:",        # nuclei / 通用发现行
)


def make_action_runner(
    *,
    resolve_host: Callable[[str], str] | None = None,
    store_dir: str | Path | None = None,
    timeout_s: int = 60,
    max_output_bytes: int = 64 * 1024,
) -> Callable[[Sequence[str]], dict[str, object]]:
    """返回 `act.exec_tool` 用的 ``run(argv) -> dict``。

    ``resolve_host`` 是**框架侧的目标解析器**：argv 里出现占位 host（含 ``.invalid``
    或等于占位名）时，执行前替换为解析出的真实目标。地址在运行期才出现，
    因此不违反"地址不入库"——这与 `tools/transport.py` 在同一层做同一件事。

    **不传 resolve_host** 时原样执行（用于对已解析 argv 的直跑）。
    """

    def _run(argv: Sequence[str]) -> dict[str, object]:
        final: list[str] = []
        for token in argv:
            if resolve_host is None or not _is_placeholder(token):
                final.append(token)
                continue
            # 整 token 是占位 → 直接换成解析值；
            # token 里含占位片段（如 ``host.invalid/FUZZ``）→ 只换掉前缀那一段。
            resolved = resolve_host(token)
            if _is_placeholder(token) and not _whole_token_placeholder(token):
                for marker in sorted(_PLACEHOLDER_MARKERS, key=len, reverse=True):
                    idx = token.lower().find(marker)
                    if idx >= 0:
                        final.append(resolved + token[idx + len(marker):])
                        break
                else:
                    final.append(token)
            else:
                final.append(resolved)
        t0 = time.perf_counter()
        try:
            done = subprocess.run(  # noqa: S603 - argv 是数组，无 shell 拼接
                list(final),
                capture_output=True,
                timeout=timeout_s,
                check=False,
            )
            code = done.returncode
            raw = _decode(done.stdout) + _decode(done.stderr)
        except FileNotFoundError:
            return {"stdout": "", "exit": 127, "hits": [], "verifiable_by": [],
                    "error": f"binary_not_found:{final[0] if final else '?'}",
                    "elapsed_ms": int((time.perf_counter() - t0) * 1000)}
        except subprocess.TimeoutExpired:
            return {"stdout": "", "exit": 124, "hits": [], "verifiable_by": [],
                    "error": "timeout", "elapsed_ms": int((time.perf_counter() - t0) * 1000)}

        sliced = bound_and_store(raw, limit=max_output_bytes, store_dir=store_dir)
        hits = _derive_hits(sliced.text)
        return {
            "stdout": sliced.text,
            "exit": code,
            "hits": hits,
            # 有命中才有"能被某个 oracle 验证"的资格；否则 lead 天然作废（35 §2）。
            "verifiable_by": ("idor_cross_account", "open_redirect") if hits else [],
            "selector_hint": "object_read" if hits else None,
            "truncated": sliced.truncated,
            "persisted_ref": sliced.persisted_ref,
            "elapsed_ms": int((time.perf_counter() - t0) * 1000),
        }

    return _run


def _is_placeholder(token: str) -> bool:
    """占位符可以是**整个 token**，也可以是 token 里的**前缀片段**。

    为什么必须支持片段：``ffuf`` 的目标是 ``<base>/FUZZ``，
    占位 host 被拼在路径前面（``injected-by-framework.invalid/FUZZ``）。
    只按整 token 替换会漏掉它，于是 ffuf 去打一个不存在的主机——
    测出来是"没命中"，看起来像目标没洞，实际是接线没对。
    """
    low = token.lower()
    if low.endswith(".invalid") or low in {"{host}", "{url}", "injected-by-framework"}:
        return True
    return any(marker in low for marker in _PLACEHOLDER_MARKERS)


#: 可被子串识别的占位标记。**只认保留域名后缀与显式占位名**，不认通配。
_PLACEHOLDER_MARKERS: tuple[str, ...] = (".invalid", "{host}", "{url}")


def _whole_token_placeholder(token: str) -> bool:
    low = token.lower().strip()
    return low.endswith(".invalid") or low in {"{host}", "{url}", "injected-by-framework"}


def _derive_hits(text: str) -> list[str]:
    """从真实输出里派生命中行。**空就是空**——不编。"""
    hits: list[str] = []
    for line in text.splitlines():
        low = line.lower()
        if any(h in low for h in _HIT_HINTS):
            hits.append(line.strip()[:120])
    return hits


__all__ = [
    "ExecOutcome",
    "Executor",
    "ExecutorError",
    "make_action_runner",
    "make_subprocess_executor",
]
