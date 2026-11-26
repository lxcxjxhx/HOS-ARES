"""容器执行器（B45）。

**只做两件事**：拼一条加固过的 `docker run` 命令行；把结果归一成 `ExecResult`。
不查库、不解析地址、不判定——地址由调用方在运行期注入（`H02 §2`）。

**第三种启动方式（B536）**：Windows 上没有 docker 时，容器引擎可能在 WSL 发行版里
（`wsl -d <distro> -- podman ...`）。这条路径的隔离**仍是容器**——命名空间由 podman
建，宿主看不到容器内写入，E1 的判据照样成立。**不许把它降级成 job-object**：
那不是宽严问题，是说错话。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
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
    #: 执行档位：``container``（docker / podman / WSL 里的 podman）或 ``job-object``
    #: （Windows 受限执行）。**不许把 job-object 写成 container**——那会让 E1 的判据变假绿；
    #: 反过来把真容器写成 job-object 同样是说错话。
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


def _engine_alive(binary: str) -> bool:
    """问一次 `info`：守护进程在**并且**是 Linux 容器模式，才算可用。

    两道判据缺一不可，各自来自一次真实误判：

    1. **守护进程在**（B200/B541）：只 `which` 得到路径，挡不住"装了 CLI 没起 daemon"
       这种最常见的 CI 形态——GitHub 的 windows runner 就是这样。
    2. **OSType 必须是 linux**（B542）：runner 上 docker 起来了，却是 **Windows 容器模式**，
       `--read-only` / `--cap-drop` 一律报 `invalid option: read-only mode is not supported`
       （exit 125）。引擎"活着"但跑不了加固参数，对我们等于不可用。

    超时给短（20s）：这条路在"选执行体"的关键路径上，不能因为引擎卡住而挂住整个命令。
    """
    try:
        proc = subprocess.run(  # noqa: S603 - 固定命令，无外部输入
            [binary, "info", "--format", "{{.ServerVersion}}|{{.OSType}}"],
            capture_output=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if proc.returncode != 0:
        return False
    text = (proc.stdout or b"").decode("utf-8", "replace").strip()
    version, _, os_type = text.partition("|")
    if not version.strip():
        return False
    # 老版本 docker 不给 OSType 时按 linux 处理（那时只有 linux 一种模式）；
    # 明确说了 windows 的一律不可用——加固项在那种模式下不被接受。
    return os_type.strip().lower() != "windows"


class DockerRunner:
    """`docker run` 的最保守形态：只读根、无网、无特权、有资源上限、退出即删。"""

    name = "docker"
    #: 隔离等级。`container` 是**唯一**能写这个值的一档：命名空间真建了。
    isolation = "container"
    #: 宿主侧比容器内超时多等这么久：让容器里的 `timeout` 先到点自己收尸，
    #: 宿主只做兜底。等一样长 = 两边同时开枪，容器那侧永远来不及留下 124。
    GRACE_S = 20

    def __init__(self, binary: str | None = None, *, assume_alive: bool = False) -> None:
        """``assume_alive=True`` 只用于**验 argv 形状**：跳过守护进程探活。

        谁该用：只调 `build_argv()` 的用例（判据是"加固项齐不齐"），它们不该因为
        这台机器上没起引擎而失败。谁不该用：任何真执行路径——那里必须走探活，
        否则就是"命令在当成服务在"（B541 的原始缺陷）。
        """
        self._binary = binary or _docker_bin()
        #: 探活结果缓存：`available()` 在选执行体时会被调用，不能每次都起进程。
        self._alive: bool | None = True if assume_alive else None

    def available(self) -> bool:
        """**命令在 ≠ 服务在**（B200，B541 在 CI 上栽了第二次）。

        以前这里只看二进制在不在。GitHub 的 windows runner **有 docker CLI 但没有
        守护进程**，于是 `select_executor()` 返回 `DockerRunner`，接下来每条命令都
        回 `docker: Error response from daemon`（exit 125）——三组用例连锁失败。

        现在问一次 `docker info`：问不到就当不可用，让调用方继续往下选
        （WSL podman → Job Object → BLOCKED）。探活结果缓存，选一次只起一个进程。
        """
        if self._binary is None:
            return False
        if self._alive is None:
            self._alive = _engine_alive(self._binary)
        return self._alive

    @property
    def _prefix(self) -> list[str]:
        """引擎调用前缀。WSL 档换掉这一项即可，其余逻辑完全共用。"""
        return [self._binary or "docker"]

    def _map_mount(self, host_path: str) -> str:
        """宿主管路径 → 引擎看得懂的路径。宿主 docker 上就是原样。"""
        return host_path

    def _with_timeout(self, argv: Sequence[str], timeout_s: int) -> list[str]:
        """把超时**放进容器里**（`timeout N cmd`），而不是只靠宿主侧杀客户端。

        只杀 `docker` / `wsl` 客户端的话，容器那一侧会接着跑（podman 由 conmon 托管，
        客户端掉线不等于容器停）——那就是"超时了但没停"，还留残留容器。
        `build_argv` 保持**不加**这一层（那条路径的判据是"加固项形状"），
        超时在 `run()` 里插到镜像之后、用户命令之前。
        """
        return ["timeout", str(max(1, int(timeout_s))), *argv]

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

        cmd = [*self._prefix, "run", "--rm",
               "--read-only", "--cap-drop", "ALL",
               "--security-opt", "no-new-privileges",
               "--user", "65534:65534",
               "--network", network,
               "--cpus", cpus, "--memory", memory, "--pids-limit", str(pids)]
        for host_path, container_path in mounts:
            src = self._map_mount(host_path)
            cmd += ["--mount", f"type=bind,src={src},dst={container_path},readonly"]
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
        # 超时插在镜像之后（镜像 = 用户命令之前的那个元素）
        cmd = [*cmd[:len(cmd) - len(argv)], *self._with_timeout(argv, timeout_s)]
        try:
            # 宿主侧多等 GRACE_S：容器内的 `timeout` 才是主判，宿主只兜底。
            proc = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout_s + self.GRACE_S,
                          encoding="utf-8", errors="replace",
                                  check=False)
        except FileNotFoundError as exc:          # 二进制在、但执行失败
            return ExecResult(None, outcome="blocked", reason=f"runtime_unavailable: {exc}")
        except subprocess.TimeoutExpired as exc:
            return ExecResult(None, stdout=_text(exc.stdout), stderr=_text(exc.stderr),
                              outcome="error", reason=f"timeout>{timeout_s}s")
        if proc.returncode == 124:                 # coreutils timeout 的固定退出码
            return ExecResult(124, proc.stdout, proc.stderr, outcome="error",
                              reason=f"timeout>{timeout_s}s（容器内 timeout 触发）",
                              isolation=self.isolation)
        return ExecResult(proc.returncode, proc.stdout, proc.stderr,
                          outcome="ok" if proc.returncode == 0 else "error",
                          reason="" if proc.returncode == 0 else f"exit={proc.returncode}",
                          isolation=self.isolation)


# ── WSL 里的容器引擎（B536）─────────────────────────────────────
#: 发行版名可覆盖；默认按 `wsl -l -q` 顺序找第一个 `podman info` 真能回话的。
WSL_ENV_DISTRO = "HOS_ARES_WSL_DISTRO"


def _wsl_exe() -> str | None:
    if sys.platform != "win32":
        return None
    return shutil.which("wsl")


def _wsl_distros(exe: str) -> list[str]:
    """列发行版。**输出是 UTF-16LE 带零字节**（Windows 侧实测）——不去零会得到
    `U\\x00b\\x00...` 这种串，拿去 `wsl -d` 必然"发行版不存在"。"""
    try:
        proc = subprocess.run([exe, "-l", "-q"], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    raw = proc.stdout or b""
    if b"\x00" in raw:                       # UTF-16
        text = raw.decode("utf-16-le", errors="replace")
    else:
        text = raw.decode("utf-8", errors="replace")
    return [line.strip() for line in text.replace("\r", "\n").split("\n") if line.strip()]


def _wsl_podman_probe(exe: str, distro: str) -> bool:
    """**命令在 ≠ 服务在**（B200 的口径在 WSL 里同样适用）：要问一次 `podman info`。"""
    try:
        proc = subprocess.run(
            [exe, "-d", distro, "--", "podman", "info", "--format", "{{.Host.OCIRuntime.Name}}"],
            capture_output=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and bool((proc.stdout or b"").strip())


class WslPodmanRunner(DockerRunner):
    """`wsl -d <distro> -- podman run ...`（B536）。

    与 `DockerRunner` **共用同一套加固 argv**：只多两个前缀元素，加固项一项不少——
    换一条命令行路径不许变成另一种宽严。

    `isolation` 仍是 `container`：命名空间由发行版里的 podman 建，容器内写 `/tmp`
    宿主看不到（E1 判据实测成立）。
    """

    name = "wsl-podman"

    def __init__(self, *, wsl_exe: str | None = None, distro: str = "") -> None:
        self._wsl = wsl_exe or _wsl_exe()
        self._distro = distro
        super().__init__(binary="podman")     # 引擎在发行版里，宿主没有这个二进制

    def available(self) -> bool:
        return bool(self._wsl and self._distro)

    @property
    def _prefix(self) -> list[str]:
        """`[wsl, -d, 发行版, --exec, podman]`。

        **必须是 `--exec` 而不是 `--`**（B536 实测）：`--` 之后的参数会被 WSL 拼成
        一条字符串交给登录 shell，于是 `python -c print(1)` 在 bash 里先被解析成
        语法错误——报出来像"容器起不来"，实际是引号在半路丢了。
        `--exec` 直接把 argv 原样传给发行版里的进程，不经过 shell。
        加固项仍由父类统一拼，这里只换调用前缀。
        """
        return [self._wsl or "wsl", "-d", self._distro, "--exec", self._binary or "podman"]

    def wsl_path(self, path: str) -> str:
        """把 Windows 路径翻成发行版里的挂载路径（`C:\\a` → `/mnt/c/a`）。

        `--mount src=` 用的是**引擎那一侧**的路径：不翻，podman 会去找一个叫
        `C:\\a` 的发行版内路径，报"no such file"，而错误看起来像"隔离失败"。
        先问 `wslpath`（符号链接、自定义挂载点它都认），问不到再按盘符兜底。
        """
        sl = chr(0x2F)
        if not path or path.startswith(sl):
            return path
        try:
            proc = subprocess.run(
                [self._wsl or "wsl", "-d", self._distro, "--", "wslpath", "-a", path],
                capture_output=True, timeout=30,
            )
            if proc.returncode == 0 and (proc.stdout or b"").strip():
                return (proc.stdout or b"").decode("utf-8", "replace").strip()
        except (OSError, subprocess.SubprocessError):
            pass
        drive, _, rest = path.partition(":")
        if len(drive) == 1 and rest:
            return f"{sl}mnt{sl}{drive.lower()}{rest.replace(chr(0x5C), sl)}"
        return path

    def _map_mount(self, host_path: str) -> str:
        return self.wsl_path(host_path)


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
    """四级：宿主 docker/podman → **WSL 里的 podman** → Windows Job Object → None。

    WSL 排在 Job Object 之前，因为它是**真容器隔离**；Job Object 只做资源上限。
    优先级按隔离强度排，不按"哪个更容易起来"排。

    **不许降级到裸跑**：没有隔离时宁可不执行，也不假装执行过。
    """
    runner = DockerRunner()
    if runner.available():
        return runner
    wsl = _wsl_exe()
    if wsl:
        wanted = os.environ.get(WSL_ENV_DISTRO, "").strip()
        for distro in ([wanted] if wanted else _wsl_distros(wsl)):
            if distro and _wsl_podman_probe(wsl, distro):
                return WslPodmanRunner(wsl_exe=wsl, distro=distro)
    try:
        job = JobObjectExecutor()
        if job.available():
            return job
    except Exception:  # noqa: BLE001 —— 平台不支持时静默跳过，不打断选择
        pass
    return None
