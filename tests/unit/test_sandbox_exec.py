"""B546 · `sh` 工具接上容器执行体（strix 执行面与 E1 隔离的接线处）。

`parity` 的 E1 早就验过"容器隔离成立"，但**产品执行路径从来没用上它**：
`hos-ares chat` 的 `sh` 工具、`shell.run` 一直用 `make_subprocess_executor()`
在宿主上起子进程。于是"能打"和"打得有边界"是分开的两件事。

这一批把两者接起来。三条判据，每条都能在本机验（不需要容器也能验前两条）：

1. **有容器档执行体 → 走容器**，`isolation` 说明出现在输出里；
2. **没有 → 显式降级到宿主**，且必须写 `isolation: host`（不静默）；
3. **要求隔离但不可用 → 拒绝执行**，返回 125 而不是偷偷在宿主跑。
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hos_ares_cli import cmd_chat  # noqa: E402
from hos_ares_core.sandbox.runner import ExecResult  # noqa: E402
from hos_ares_core.workbench.sandbox_exec import (  # noqa: E402
    INTERP_ARGV,
    NO_SANDBOX_EXIT,
    WORK_DIR,
    make_sandbox_executor,
    unavailable_executor,
)


# ── 1：形状（不需要容器）──────────────────────────────────────
def test_interpreter_map_matches_the_sandbox_image() -> None:
    """`python:3.13-slim` 里有 python 与 sh，**没有 bash**：bash 必须落到 `sh -c`。"""
    assert INTERP_ARGV["python"] == ("python", "-c")
    assert INTERP_ARGV["bash"] == ("sh", "-c"), "slim 镜像没有 bash"
    for interp in ("sh", "cmd", "cmd.exe"):
        assert INTERP_ARGV[interp] == ("sh", "-c")


def test_blocked_result_never_becomes_success() -> None:
    """`blocked` 不许被抹成 exit 0——那会把"没跑"说成"跑成功了"。"""
    from hos_ares_core.sandbox.runner import ExecResult

    captured: dict[str, object] = {}

    class _Blocked:
        isolation = "container"
        name = "blocked"

        def available(self) -> bool:
            return True

        def run(self, *a: object, **kw: object) -> ExecResult:
            captured.update(kw)
            return ExecResult(None, outcome="blocked", reason="runtime_unavailable")

    runner = make_sandbox_executor(_Blocked(), image="img")
    code, _out, err = runner("print(1)", "python", "", 5, {})
    assert code == NO_SANDBOX_EXIT, code
    assert "runtime_unavailable" in err


def test_work_dir_is_an_absolute_posix_path() -> None:
    """挂载点必须是绝对 POSIX 路径：它出现在引擎的 `--mount dst=` 里。"""
    assert WORK_DIR.startswith(chr(0x2F))
    assert "\\" not in WORK_DIR


def test_mounts_and_env_reach_the_engine() -> None:
    seen: dict[str, object] = {}

    class _Recorder:
        isolation = "container"
        name = "recorder"

        def available(self) -> bool:
            return True

        def run(self, argv: Sequence[str], *, image: str,
                mounts: Sequence[tuple[str, str]] = (),
                env: Sequence[tuple[str, str]] = (),
                timeout_s: int = 120) -> ExecResult:  # noqa: ARG002
            seen.update({"argv": list(argv), "mounts": list(mounts), "env": list(env)})
            return ExecResult(0, "ok", "")

    runner = make_sandbox_executor(_Recorder(), image="img", mounts=[("HOST", WORK_DIR)])
    runner("echo x", "sh", "HOST", 30, {"A": "1"})
    assert seen["argv"] == ["sh", "-c", "echo x"]
    assert seen["mounts"] == [("HOST", WORK_DIR)]
    assert seen["env"] == [("A", "1")]


# ── 2：三档决策（monkeypatch 掉环境探测）──────────────────────
def _patch_executor(monkeypatch: pytest.MonkeyPatch, value: object) -> None:
    import hos_ares_core.sandbox.runner as R

    monkeypatch.setattr(R, "select_executor", lambda: value)


def test_container_tier_is_chosen_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Container:
        isolation = "container"
        name = "probe"

        def available(self) -> bool:
            return True

        def run(self, *a: object, **kw: object) -> object:
            raise AssertionError("决策函数不该执行命令")

    _patch_executor(monkeypatch, _Container())
    monkeypatch.delenv("HOS_ARES_REQUIRE_SANDBOX", raising=False)
    _runner, note = cmd_chat._chat_executor(ROOT)
    assert "isolation: container" in note
    assert WORK_DIR in note


def test_host_fallback_is_explicit_not_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_executor(monkeypatch, None)
    monkeypatch.delenv("HOS_ARES_REQUIRE_SANDBOX", raising=False)
    _runner, note = cmd_chat._chat_executor(ROOT)
    assert "isolation: host" in note
    assert "副作用是真的" in note, "降级必须说清后果"


def test_require_sandbox_refuses_to_run_on_host(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_executor(monkeypatch, None)
    monkeypatch.setitem(os.environ, "HOS_ARES_REQUIRE_SANDBOX", "1")
    runner, note = cmd_chat._chat_executor(ROOT)
    assert "isolation: none" in note
    code, out, err = runner("echo nope", "sh", str(ROOT), 5, {})
    assert code == NO_SANDBOX_EXIT
    assert "拒绝在宿主执行" in err
    assert out == ""


def test_job_object_is_not_treated_as_container(monkeypatch: pytest.MonkeyPatch) -> None:
    """Job Object 只做资源上限，不给文件系统隔离：不能当容器档用。"""

    class _Job:
        isolation = "job-object"
        name = "job-object"

        def available(self) -> bool:
            return True

        def run(self, *a: object, **kw: object) -> object:
            raise AssertionError("不允许执行")

    _patch_executor(monkeypatch, _Job())
    monkeypatch.delenv("HOS_ARES_REQUIRE_SANDBOX", raising=False)
    _runner, note = cmd_chat._chat_executor(ROOT)
    assert "isolation: host" in note, note


# ── 3：端到端（有容器时才验真执行）────────────────────────────
def test_real_container_run_if_available() -> None:
    from hos_ares_core.sandbox.runner import select_executor

    executor = select_executor()
    if executor is None or getattr(executor, "isolation", "") != "container":
        pytest.skip("本机没有容器档执行体")
    runner = make_sandbox_executor(executor, image="python:3.13-slim")
    code, out, _err = runner("print('container-ran')", "python", "", 120, {})
    assert code == 0, code
    assert "container-ran" in out
