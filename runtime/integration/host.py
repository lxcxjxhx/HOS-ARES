# -*- coding: utf-8 -*-
"""
host.py — RuntimeHost 类：Android 上 proot 容器宿主集成层（占位实现）

作用：
    为上层（gateway / agents）提供统一的 Linux Runtime 接口，用于在 Android 上
    启动 reasonix-proot-app 提供的 proot 容器，并在其中执行命令。

接口：
    - start()            启动 proot 容器（并执行 Alpine bootstrap 初始化）
    - stop()             停止容器、清理资源
    - exec(command)      在容器内执行任意命令并返回输出
    - is_running()       判断容器是否在运行

真实实现思路：
    当前为纯 Python 脚手架，使用 subprocess 调用本机（开发机）的 proot 做占位。
    在真实 Android 环境：
      - proot 二进制由 reasonix-proot-app 打包/内置到 APK，通常是 native 可执行文件，
        需通过 adb / Android 进程以绝对路径调用（例如 app 私有目录下的 ./proot）。
      - rootfs 为 runtime/alpine/ 说明的 Alpine rootfs，位于应用私有目录。
      - 容器本质是一个长时间运行的 proot 进程（shell），exec() 通过向该进程
        的 stdin 写入命令，或每次启动 `proot -r <rootfs> /bin/sh -c "<cmd>"` 执行。
      - stop() 应发送退出信号并回收进程。
"""

import os
import shlex
import subprocess
from typing import Optional


class RuntimeHost:
    """proot 容器宿主：管理 Android Linux Runtime 的启动、停止与命令执行。"""

    def __init__(
        self,
        rootfs: str,
        proot_bin: str = "proot",
        bootstrap_script: Optional[str] = None,
        workdir: str = "/work",
        user: str = "agent",
    ):
        """
        参数（占位）：
            rootfs          : Alpine rootfs 根目录路径（容器根文件系统）。
            proot_bin       : proot 可执行文件路径。
                              真实环境为 reasonix-proot-app 打包的 native 二进制，
                              开发机调试时可为系统 PATH 中的 proot。
            bootstrap_script: 容器内初始化脚本（runtime/alpine/bootstrap.sh）路径，可选。
            workdir         : 容器内默认工作目录（通常由 bootstrap 创建）。
            user            : 容器内执行命令使用的用户（非 root，安全考虑）。
        """
        self.rootfs = rootfs
        self.proot_bin = proot_bin
        self.bootstrap_script = bootstrap_script
        self.workdir = workdir
        self.user = user

        # 占位：记录容器主进程（真实环境为 proot 容器的 shell 进程）。
        self._container_proc: Optional[subprocess.Popen] = None

    # ------------------------------------------------------------------
    # 内部：组装 proot 命令前缀
    # 真实实现思路：reasonix-proot-app 会按需附加 -0 / --kill-on-exit /
    #   共享目录绑定 (-b) / 端口透传等参数。此处保留最小占位。
    # ------------------------------------------------------------------
    def _proot_prefix(self) -> list:
        prefix = [
            self.proot_bin,
            "-r",
            self.rootfs,          # 指定根文件系统
            "-b",
            "/proc",              # 挂载宿主 /proc（占位）
            "-b",
            "/dev",               # 挂载宿主 /dev（占位）
            "-w",
            self.workdir,         # 进入工作目录
        ]
        # 真实环境常使用 -0 模拟 root 用户；此处根据 user 是否为 root 决定（占位）。
        if self.user == "root":
            prefix.append("-0")
        return prefix

    # ------------------------------------------------------------------
    # start(): 启动容器
    # 真实实现思路：
    #   1) 校验 rootfs 与 proot 二进制存在。
    #   2) 以 `proot -r <rootfs> /bin/sh` 启动一个长期存活的容器 shell 进程，
    #      持有该进程引用（self._container_proc），后续 exec() 通过其交互执行。
    #   3) 可选：在容器内执行 bootstrap.sh 完成 Alpine 环境初始化。
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self.is_running():
            raise RuntimeError("Runtime 已在运行，请先 stop()。")

        if not os.path.isdir(self.rootfs):
            raise FileNotFoundError(f"rootfs 不存在: {self.rootfs}")

        print(f"[host] 启动 proot 容器 (rootfs={self.rootfs}) ...")

        # 占位实现：真实环境在此启动持久容器进程。
        # 例（伪代码）：
        #   cmd = self._proot_prefix() + ["/bin/sh", "-i"]
        #   self._container_proc = subprocess.Popen(
        #       cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        #       stderr=subprocess.STDOUT, text=True,
        #   )

        # 执行 bootstrap 初始化（可选）
        if self.bootstrap_script and os.path.isfile(self.bootstrap_script):
            self.exec(f"/bin/sh {shlex.quote(self.bootstrap_script)}")

        print("[host] 容器启动完成（占位）。")

    # ------------------------------------------------------------------
    # exec(): 在容器内执行命令
    # 真实实现思路：
    #   方式一（交互式）：向 start() 持有的容器 shell stdin 写入命令并读取输出。
    #   方式二（一次性）：每次执行 `proot -r <rootfs> /bin/sh -c "<cmd>"`。
    #   此处采用方式二做占位，简单且无状态。
    # ------------------------------------------------------------------
    def exec(self, command: str, timeout: Optional[float] = None) -> str:
        if not self.is_running():
            raise RuntimeError("Runtime 未运行，请先 start()。")

        # 占位实现：开发机上直接以 proot 一次性执行（真实环境换成交互式方式）。
        cmd = self._proot_prefix() + ["/bin/sh", "-c", command]

        print(f"[host] exec: {command}")
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return f"[host] 命令执行超时: {command}"

        # 返回合并的标准输出与错误输出（占位），真实实现可分别返回。
        output = result.stdout or ""
        if result.stderr:
            output += ("\n" if output else "") + result.stderr
        return output.strip()

    # ------------------------------------------------------------------
    # stop(): 停止容器
    # 真实实现思路：向容器 shell 进程发送退出命令/信号，等待其结束并回收。
    # ------------------------------------------------------------------
    def stop(self) -> None:
        if not self.is_running():
            print("[host] 容器未运行，无需停止。")
            return

        print("[host] 停止 proot 容器 ...")
        # 占位实现：真实环境发送 SIGTERM / 退出命令并等待。
        if self._container_proc is not None:
            # self._container_proc.terminate()
            # self._container_proc.wait(timeout=10)
            self._container_proc = None

        print("[host] 容器已停止（占位）。")

    # ------------------------------------------------------------------
    # is_running(): 判断容器是否在运行
    # 真实实现思路：检查 self._container_proc 是否为存活进程（poll() is None）。
    #   由于脚手架未真正拉起进程，占位实现根据 _container_proc 是否被设置来判断。
    # ------------------------------------------------------------------
    def is_running(self) -> bool:
        # 占位实现：
        #   return self._container_proc is not None and self._container_proc.poll() is None
        return self._container_proc is not None


# ----------------------------------------------------------------------
# 使用示例（占位，可在开发机上验证结构）
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    # 开发机占位：rootfs 指向 setup.ps1 准备好的目录，proot 为系统 PATH 中的 proot。
    host = RuntimeHost(
        rootfs=os.environ.get("HOS_ROOTFS", "./rootfs"),
        bootstrap_script=os.environ.get("HOS_BOOTSTRAP", "alpine/bootstrap.sh"),
    )
    host.start()
    print(host.exec("/bin/sh -c 'cat /etc/alpine-release; whoami'"))
    host.stop()
