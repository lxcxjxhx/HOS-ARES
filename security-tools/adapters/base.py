# -*- coding: utf-8 -*-
"""
security-tools/adapters/base.py — Agent 统一适配器基类

职责：
    - 为每个从 GitHub 拉取的安全 Agent 提供统一封装（真实源码调用）
    - 定位 agents/<name> 下的真实入口，构造正确的命令与 PYTHONPATH
    - 提供 run(target, **kwargs) -> AdapterResult 的统一调用接口
    - 供上层 ToolExecutor / Skill Registry 按名称调度

与脚手架占位（tool_executor 里检查 shutil.which）不同：
    本层直接指向拉取的 agents/ 目录中的真实源码，不再依赖全局 PATH。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class AdapterResult:
    """适配器执行结果。"""

    agent: str                          # agent 名称
    target: str                         # 目标路径/仓库/URL
    status: str                         # ok / error / not_available
    output: str = ""                    # 原始输出（stdout + stderr）
    returncode: Optional[int] = None    # 子进程返回码
    findings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def project_root() -> Path:
    """返回 HOS-ARES 项目根目录。"""
    return Path(__file__).resolve().parents[2]


def agents_root() -> Path:
    """返回 agents/ 根目录。"""
    return project_root() / "agents"


class AgentAdapter:
    """所有安全 Agent 适配器的基类。

    子类需实现：
        build_cmd(target, kwargs) -> Tuple[List[str], Dict[str, str]]
    返回 (命令列表, 额外环境变量)。命令第一项为解释器（python / uv 等）。
    """

    # 子类覆盖
    name: str = "base"
    agent_dir: str = ""                 # agents/ 下的目录名（默认等于 name）
    entry: List[str] = ()               # 相对 src_root 的入口（如 ["repoaudit.py"]）
    src_root: str = ""                  # 相对 agent_dir 的源码根（默认 ""）

    def __init__(self, timeout: int = 600) -> None:
        self.timeout = timeout

    # ------------------------------------------------------------------
    # 子类需实现的接口
    # ------------------------------------------------------------------
    def build_cmd(self, target: str, kwargs: Dict[str, Any]) -> Tuple[List[str], Dict[str, str]]:
        """构造真实调用命令与环境变量。必须由子类实现。"""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # 公共能力
    # ------------------------------------------------------------------
    def agent_path(self) -> Path:
        """返回该 agent 源码目录绝对路径。"""
        return agents_root() / (self.agent_dir or self.name)

    def src_path(self) -> Path:
        """返回该 agent 源码根目录（用于 PYTHONPATH）。"""
        base = self.agent_path()
        return base / self.src_root if self.src_root else base

    def interpreter(self) -> str:
        """返回解释器。优先用当前 Python，其次 python3 / python。"""
        for cand in (sys.executable, "python3", "python"):
            if cand and shutil.which(cand):
                return cand
        return sys.executable or "python"

    def env(self, target: str, kwargs: Dict[str, Any]) -> Dict[str, str]:
        """构造运行环境：注入源码根到 PYTHONPATH。"""
        env = dict(os.environ)
        src = str(self.src_path())
        old_pypath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = src if not old_pypath else (src + os.pathsep + old_pypath)
        env.setdefault("HOS_AGENT", self.name)
        return env

    def run(self, target: str, **kwargs: Any) -> AdapterResult:
        """执行该 agent（真实源码调用）。"""
        # 先校验源码是否就位
        if not self.src_path().is_dir():
            return AdapterResult(
                agent=self.name, target=target, status="not_available",
                output=f"源码目录不存在（未拉取？）: {self.src_path()}",
            )
        try:
            cmd, env = self.build_cmd(target, kwargs)
        except ValueError as exc:
            return AdapterResult(
                agent=self.name, target=target, status="error", output=str(exc)
            )
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.timeout, env=env
            )
        except subprocess.TimeoutExpired as exc:
            return AdapterResult(
                agent=self.name, target=target, status="error",
                output=f"执行超时（{self.timeout}s）: {exc}",
            )
        except OSError as exc:
            return AdapterResult(
                agent=self.name, target=target, status="error",
                output=f"执行失败: {exc}",
            )
        output = (proc.stdout or "") + (proc.stderr or "")
        return AdapterResult(
            agent=self.name, target=target,
            status="ok" if proc.returncode == 0 else "error",
            output=output, returncode=proc.returncode,
        )
