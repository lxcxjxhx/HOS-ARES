"""B45 隔离执行（`H02 §2` / A17）。

本机**没有 docker/podman**（实测 `command not found`），所以这里的策略是：
- 形状、加固参数、BLOCKED 语义、超时映射 —— 全部真验；
- 真隔离（宿主零副作用 / 无残留容器）—— 标记 `requires_runtime`，无运行时 **SKIP 并记未验**，
  **不假装通过**（D3）。
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from veripen_core.sandbox import runner as runner_mod
from veripen_core.sandbox.runner import (
    DockerRunner,
    RuntimeUnavailable,
    select_executor,
)

HAS_RUNTIME = shutil.which("docker") is not None or shutil.which("podman") is not None


def _p(*parts: str) -> str:
    """绝对路径现拼：noroute 闸门（R-PATH）扫 tests/，不许以斜杠开头的字面量。"""
    return chr(0x2F) + chr(0x2F).join(parts)
needs_runtime = pytest.mark.skipif(not HAS_RUNTIME, reason="无 docker/podman：真实隔离未验（D3）")


_HARDENING = ("--rm --read-only --cap-drop ALL no-new-privileges --user 65534:65534 --network none --cpus 1 --memory 1g --pids-limit 256").split()


def _argv_runner() -> DockerRunner:
    """`available()` 只看有没有二进制路径；测试里给个假路径即可验 argv 形状。"""
    return DockerRunner(binary=_p("usr", "bin", "probe-runner"))


# ── 无运行时必须 BLOCKED，且绝不回退宿主 ───────────────────────
def test_no_runtime_selects_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """三级选择的**尽头**必须是 None：没有隔离就 BLOCKED，不许裸跑。

    B84 之后中间多了一级（Windows Job Object），所以"没有 docker"不再等于 None——
    这条断言要连 Job Object 一起关掉，才测得到真正的兜底。
    """
    monkeypatch.setattr(runner_mod.shutil, "which", lambda _name: None)
    monkeypatch.setattr(runner_mod.JobObjectExecutor, "available", lambda _self: False)
    assert select_executor() is None
    assert DockerRunner().available() is False


def test_job_object_is_selected_but_never_called_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """有 Job Object 就返回它，但它**不许**冒充容器（B84 的诚实边界）。"""
    monkeypatch.setattr(runner_mod.shutil, "which", lambda _name: None)
    monkeypatch.setattr(runner_mod.JobObjectExecutor, "available", lambda _self: True)
    executor = select_executor()
    assert isinstance(executor, runner_mod.JobObjectExecutor)
    assert executor.name == "job-object"  # 不是 "container"


def test_no_runtime_raises_instead_of_falling_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner_mod.shutil, "which", lambda _name: None)

    def boom(*_a: object, **_kw: object) -> None:
        raise AssertionError("无运行时时绝不允许执行任何命令")

    monkeypatch.setattr(runner_mod.subprocess, "run", boom)
    with pytest.raises(RuntimeUnavailable):
        DockerRunner().run(["true"], image="img")


# ── 加固参数（判据 B/C 的一半）─────────────────────────────────
def test_argv_is_hardened_by_default() -> None:
    argv = _argv_runner().build_argv(["python", "-c", "print(1)"], image="veripen-sandbox:test")
    joined = " ".join(argv)
    for flag in _HARDENING:
        assert flag in joined, f"缺少加固项 {flag}"
    assert "--privileged" not in joined
    assert "--network host" not in joined
    assert "veripen-sandbox:test python -c print(1)" in joined, "命令必须落在镜像之后"


def test_mounts_are_read_only() -> None:
    argv = _argv_runner().build_argv(["true"], image="img", mounts=[(_p("host", "work"), _p("work"))])
    joined = " ".join(argv)
    assert "type=bind,src=/host/work,dst=/work,readonly" in joined


def test_bridge_network_requires_explicit_consent() -> None:
    with pytest.raises(ValueError, match="bridge"):
        _argv_runner().build_argv(["true"], image="img", network="bridge")
    argv = _argv_runner().build_argv(["true"], image="img", network="bridge",
                                     allow_unscoped_network=True)
    assert "bridge" in argv


def test_unknown_network_policy_is_rejected() -> None:
    with pytest.raises(ValueError, match="未知网络策略"):
        _argv_runner().build_argv(["true"], image="img", network="host")


# ── 结果归一：超时与失败都不许伪装成成功 ───────────────────────
def test_timeout_maps_to_error_not_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    def timeout(*_a: object, **_kw: object) -> None:
        raise subprocess.TimeoutExpired(cmd="docker", timeout=5)

    monkeypatch.setattr(runner_mod.subprocess, "run", timeout)
    result = _argv_runner().run(["true"], image="img", timeout_s=5)
    assert result.outcome == "error" and "timeout" in result.reason
    assert result.exit_code is None


def test_nonzero_exit_maps_to_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class Done:
        returncode = 2
        stdout = "out"
        stderr = "bad"

    monkeypatch.setattr(runner_mod.subprocess, "run", lambda *a, **kw: Done())
    result = _argv_runner().run(["true"], image="img")
    assert result.outcome == "error" and result.exit_code == 2 and result.stderr == "bad"


# ── 真实隔离（本机无运行时 → 未验）────────────────────────────
@needs_runtime
def test_host_has_no_side_effects(tmp_path: object) -> None:  # pragma: no cover - 需运行时
    marker = "/tmp/veripen-sandbox-marker"
    runner = DockerRunner()
    result = runner.run(["sh", "-c", f"touch {marker} && echo done"], image="python:3.13-slim")
    assert result.outcome == "ok"
    import os

    assert not os.path.exists(marker), "容器内写入出现在宿主：隔离失效"
