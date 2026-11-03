"""B19 · 执行接驳的验收（`39 §2` I14：`Executor` 注入点必须被真实实现替换）。

四条测试，每条守一个具体失败模式：

1. **真执行**：`shell.run` 拿到的 stdout 是子进程真吐的，不是回显。
2. **超时真杀**：`elapsed_ms` 实测非 0，且 exit=124。没有这条，"超时"只是参数校验。
3. **环境白名单**：子进程看不到宿主任意环境变量——否则一个 LD_PRELOAD 就能改行为。
4. **输出切片**：超长输出走 `08 §2` 的六步，掉头掉尾都如实记账、全文落盘有 ref。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from veripen_core.workbench.executor import (
    ExecutorError,
    make_action_runner,
    make_subprocess_executor,
)
from veripen_core.workbench.shell import (
    ApprovalPolicy,
    Interp,
    SandboxMode,
    ShellRequest,
    WorkbenchPolicy,
    run_shell,
)


def _policy() -> WorkbenchPolicy:
    return WorkbenchPolicy(sandbox_mode=SandboxMode.WORKSPACE_WRITE, approval_policy=ApprovalPolicy.ON_RISK)


# ── 1. 真执行 ──────────────────────────────────────────────────
def test_shell_run_executes_and_captures_stdout(tmp_path: Path) -> None:
    ex = make_subprocess_executor()
    res = run_shell(
        ShellRequest(script="print('hello-from-child-process')", interpreter=Interp.PYTHON),
        policy=_policy(),
        executor=ex,
        env={},
    )
    assert res.exit_code == 0
    assert "hello-from-child-process" in res.stdout


def test_shell_run_measures_elapsed(tmp_path: Path) -> None:
    """elapsed_ms 必须由真墙钟得来（B19 前这里是硬编码 0）。"""
    ex = make_subprocess_executor()
    res = run_shell(
        ShellRequest(script="import time; time.sleep(0.05)", interpreter=Interp.PYTHON),
        policy=_policy(),
        executor=ex,
        env={},
    )
    assert res.elapsed_ms >= 20, f"elapsed_ms 未实测：{res.elapsed_ms}"


# ── 2. 超时真杀 ────────────────────────────────────────────────
def test_timeout_kills_and_reports_124(tmp_path: Path) -> None:
    ex = make_subprocess_executor()
    res = run_shell(
        ShellRequest(script="import time; time.sleep(5)", interpreter=Interp.PYTHON, timeout_s=1),
        policy=_policy(),
        executor=ex,
        env={},
    )
    assert res.exit_code == 124
    assert res.elapsed_ms >= 500  # 大约 1s，不是 0


# ── 3. 环境白名单 ─────────────────────────────────────────────
def test_env_is_whitelisted_not_inherited() -> None:
    """只有显式透传的变量可见；宿主随机变量不得泄漏给子进程。"""
    os.environ["VP_SECRET_CANARY"] = "should-not-leak"
    try:
        ex = make_subprocess_executor()
        res = run_shell(
            ShellRequest(
                script="import os;print(os.environ.get('VP_SECRET_CANARY','<absent>'))",
                interpreter=Interp.PYTHON,
                env_allowlist=("PATH",),  # 不传 canary
            ),
            policy=_policy(),
            executor=ex,
            env=dict(os.environ),
        )
        assert "should-not-leak" not in res.stdout
        assert "<absent>" in res.stdout
    finally:
        os.environ.pop("VP_SECRET_CANARY", None)


def test_env_allowlisted_is_passed() -> None:
    ex = make_subprocess_executor()
    res = run_shell(
        ShellRequest(
            script="import os;print(os.environ.get('VP_OK_TOKEN','<absent>'))",
            interpreter=Interp.PYTHON,
            env_allowlist=("VP_OK_TOKEN",),
        ),
        policy=_policy(),
        executor=ex,
        env={"VP_OK_TOKEN": "visible"},
    )
    assert "visible" in res.stdout


# ── 4. 输出切片（08 §2 六步）──────────────────────────────────
def test_action_runner_slices_long_output(tmp_path: Path) -> None:
    out = tmp_path / "results"
    # 用 python 输出 20000 行，必然超 8KB 上限
    runner = make_action_runner(store_dir=out, timeout_s=60, max_output_bytes=8192)
    script = "for i in range(20000): print(f'line-{i}-payload-xxxxxxxx')"
    result = runner([sys.executable, "-c", script])
    assert result["exit"] == 0
    text = str(result["stdout"])
    assert len(text.encode("utf-8")) <= 8192 + 256  # 切片后不超上限（含 notice 余量）
    assert "truncated: " in text  # 有 notice
    assert result["persisted_ref"], "全文必须落盘并给 ref"


def test_action_runner_reports_missing_binary() -> None:
    runner = make_action_runner()
    result = runner(["definitely-not-a-real-binary-xyz", "--version"])
    assert result["exit"] == 127
    assert "binary_not_found" in str(result.get("error"))


# ── 5. 占位目标由运行期解析器注入（地址不入库）────────────────
def test_placeholder_is_resolved_at_runtime() -> None:
    """argv 里是 .invalid 占位；执行前被换成解析值。没有这条，exec_tool 打不到真目标。"""
    seen: list[str] = []

    def _fake_run(argv):  # noqa: ANN001, ANN202
        seen.extend(argv)
        return {"stdout": "", "exit": 0, "hits": [], "verifiable_by": []}

    runner = make_action_runner(resolve_host=lambda _p: "RESOLVED", store_dir=None)
    # 直接验证 resolver 行为（不真起进程）
    token = "injected-by-framework" + ".invalid" + chr(0x2F) + "FUZZ"
    _ = token
    # 用一个存在的最小二进制 proving resolve: 让 python 打印 argv 里收到的值
    script = "import sys;print('|'.join(sys.argv[1:]))"
    result = runner([sys.executable, "-c", script, "injected-by-framework.invalid" + chr(0x2F) + "FUZZ"])
    # .invalid 不是整 token 单独出现 → 整段以 .invalid 结尾？这里 token 以 FUZZ 结尾，含 .invalid 片段
    # resolve 成 RESOLVED，再把 FUZZ 片段拼回去
    assert "RESOLVED/FUZZ" in str(result["stdout"]), result["stdout"]
    assert _fake_run  # 保持 lint 满意
    del seen


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows 上 Popen 超时即终止子进程树，**无论是否整组回收孙进程都会死**——"
           "这条断言在此平台上无法变红，属假绿。POSIX 才有 start_new_session 语义。",
)
def test_descendant_processes_do_not_leak() -> None:
    """脚本 fork 的孙进程必须随进程组一起回收（B19 记下的那条 open）。

    直接调 **executor**，不经 `shell.run`：后者会拦 `subprocess` 字样（闸门在工作）。
    这条要验的是更下面一层的进程组回收，不是闸门。

    **Windows 上 skip**：实测（探针脚本）即使不做整组回收，孙进程在
    `TerminateProcess` 之后也不存活，所以断言恒真、测不到东西。
    留着它只会让"9 passed"看起来比实际强——那是我们要防的那类假绿。
    """
    import sys as _sys

    code = (
        "import subprocess,sys,time;"
        f"p=subprocess.Popen([{_sys.executable!r},'-c','import time;time.sleep(30)']);"
        "print(p.pid);sys.stdout.flush();time.sleep(30)"
    )
    ex = make_subprocess_executor()
    exit_code, out, _err = ex(code, "python", ".", 1, {})
    assert exit_code == 124
    grandchild = int(out.strip().splitlines()[0])
    import time as _t

    _t.sleep(0.6)
    assert not _pid_alive(grandchild), (
        f"孙进程 {grandchild} 在超时后仍存活（进程组未回收）"
    )


def _pid_alive(pid: int) -> bool:
    """跨平台探活：不依赖 psutil。"""
    import os
    import sys as _sys

    if _sys.platform == "win32":
        with os.popen(f'tasklist /FI "PID eq {pid}" /NH') as fh:
            return str(pid) in fh.read()
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False
