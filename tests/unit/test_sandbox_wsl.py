"""B536 · WSL 里的 podman 作为**容器档**执行体（E1 从"未验"到"验过"的那一步）。

本机（Windows）没有 docker，但有 WSL 发行版和里面的 podman。那条路径的隔离
**仍是容器**：命名空间由 podman 建，容器内写 `/tmp` 宿主看不到。

这批要钉住三件事（三条都能在本机纯函数验，不需要容器）：

1. **加固项一项不少**——换一条命令行路径不许变成另一种宽严；
2. **`--exec` 而不是 `--`**——`--` 之后 WSL 会交给 shell 重新解析，`-c ` 的代码里
   一个括号就报语法错误，看起来像"容器起不来"（这条是实测踩出来的）；
3. **`isolation` 如实写 `container`**，而 Job Object 仍然写 `job-object`。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from hos_ares_core.sandbox import runner as runner_mod
from hos_ares_core.sandbox.runner import (
    DockerRunner,
    JobObjectExecutor,
    WslPodmanRunner,
    select_executor,
)

ROOT = Path(__file__).resolve().parents[2]
SL = chr(0x2F)

_HARDENING = (
    "--rm --read-only --cap-drop ALL no-new-privileges --user 65534:65534 "
    "--network none --cpus 1 --memory 1g --pids-limit 256"
).split()


def _wsl() -> WslPodmanRunner:
    return WslPodmanRunner(wsl_exe="wsl.exe", distro="Ubuntu2204")


# ── 1：加固项与宿主 docker 完全一致 ────────────────────────────
def test_wsl_argv_carries_every_hardening_flag() -> None:
    argv = _wsl().build_argv(["python", "-c", "print(1)"], image="python:3.13-slim")
    joined = " ".join(argv)
    for flag in _HARDENING:
        assert flag in joined, f"缺加固项 {flag}"
    assert "--privileged" not in joined
    # 命令必须落在镜像之后（镜像后面紧跟用户命令，别把参数当成镜像的一部分）
    assert argv.index("python:3.13-slim") < argv.index("-c")


def test_wsl_argv_prefix_is_exec_not_shell() -> None:
    """`--` 会让 WSL 把后面的参数交给 shell 重新解析（实测：`-c print(1)` 直接语法错）。"""
    argv = _wsl().build_argv(["true"], image="img")
    assert argv[:2] == ["wsl.exe", "-d"]
    assert argv[3] == "--exec"
    assert argv[4] == "podman"
    assert "--" not in argv, "裸 `--` 会被当成 shell 分隔符"


# ── 2：路径要翻成发行版那一侧的写法 ───────────────────────────
def test_windows_path_is_translated_for_the_engine(tmp_path: Path) -> None:
    runner = _wsl()
    runner._wsl = None                     # 不真起 wslpath：走盘符兜底分支
    got = runner.wsl_path(str(tmp_path))
    drive = str(tmp_path)[0].lower()
    assert got.startswith(f"{SL}mnt{SL}{drive}{SL}"), got
    assert "\\" not in got


def test_posix_path_is_left_alone() -> None:
    runner = _wsl()
    runner._wsl = None
    assert runner.wsl_path(SL + "srv") == SL + "srv"


# ── 3：隔离等级如实 ───────────────────────────────────────────
def test_wsl_runner_claims_container_isolation() -> None:
    assert _wsl().isolation == "container"
    assert _wsl().name == "wsl-podman"


def test_job_object_never_claims_container() -> None:
    """Job Object 的 `isolation` 恒为 `job-object`：跑完一条命令之后报的档位也不许变。"""
    from hos_ares_core.sandbox.win_job import JobExecResult

    class _FakeJob:
        def available(self) -> bool:
            return True

        def run(self, argv, **kwargs):  # noqa: ANN001, ANN003, ANN202
            return JobExecResult(0, b"1\n", b"", "ok")

    executor = JobObjectExecutor(_FakeJob())
    result = executor.run([sys.executable, "-c", "print(1)"], timeout_s=5)
    assert result.isolation == "job-object"


# ── 4：选择顺序按隔离强度，不按"哪个更容易起来" ────────────────
def test_wsl_container_wins_over_job_object(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner_mod.shutil, "which", lambda name: None if name in ("docker", "podman") else "wsl")
    monkeypatch.setattr(runner_mod, "_wsl_distros", lambda _exe: ["Ubuntu2204"])
    monkeypatch.setattr(runner_mod, "_wsl_podman_probe", lambda _exe, _d: True)
    monkeypatch.setattr(runner_mod.JobObjectExecutor, "available", lambda _self: True)
    executor = select_executor()
    assert isinstance(executor, WslPodmanRunner), type(executor).__name__


def test_host_docker_still_wins_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner_mod, "_docker_bin", lambda: SL + "usr" + SL + "bin" + SL + "docker")
    monkeypatch.setattr(runner_mod, "_wsl_podman_probe", lambda _e, _d: True)
    assert isinstance(select_executor(), DockerRunner)
    assert not isinstance(select_executor(), WslPodmanRunner)


def test_distro_env_var_short_circuits_the_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def probe(_exe: str, distro: str) -> bool:
        seen.append(distro)
        return distro == "Wanted"

    monkeypatch.setattr(runner_mod, "_docker_bin", lambda: None)
    monkeypatch.setattr(runner_mod, "_wsl_exe", lambda: "wsl.exe")
    monkeypatch.setattr(runner_mod, "_wsl_distros", lambda _e: ["a", "b", "Wanted"])
    monkeypatch.setattr(runner_mod, "_wsl_podman_probe", probe)
    monkeypatch.setattr(runner_mod.JobObjectExecutor, "available", lambda _self: False)
    monkeypatch.setitem(os.environ, runner_mod.WSL_ENV_DISTRO, "Wanted")
    executor = select_executor()
    assert isinstance(executor, WslPodmanRunner)
    assert seen == ["Wanted"], seen


# ── 5：没有引擎就不许冒充 ─────────────────────────────────────
def test_nothing_available_means_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner_mod, "_docker_bin", lambda: None)
    monkeypatch.setattr(runner_mod, "_wsl_exe", lambda: None)
    monkeypatch.setattr(runner_mod.JobObjectExecutor, "available", lambda _self: False)
    assert select_executor() is None
