# -*- coding: utf-8 -*-
"""
security-tools/adapters/__init__.py — 安全 Agent 适配器注册中心

本模块为从 GitHub 拉取的真实安全 Agent 提供统一调用层：
    RepoAudit / DeepAudit / Argus / Strix / PentestGPT

每个适配器定位 agents/<name> 下的真实源码入口，构造正确的命令与
PYTHONPATH，供上层 ToolExecutor / Skill Registry 按名称调度。

对外接口：
    run_agent(name, target, **kwargs) -> AdapterResult
    list_agents() -> List[str]
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from .base import AdapterResult, AgentAdapter

# 各 agent 的 LLM 相关环境变量名（缺省时给出提示而非静默失败）
_LLM_ENV_HINTS: Dict[str, Tuple[str, ...]] = {
    "repoaudit": ("REPOAUDIT_MODEL", "LLM_API_KEY", "OPENAI_API_KEY"),
    "strix": ("STRIX_LLM", "LLM_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"),
    "pentestgpt": ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"),
}


def _require_env(name: str, keys: Tuple[str, ...]) -> None:
    """若给定的环境变量一个都没有设置，抛错提示。"""
    if keys and not any(os.environ.get(k) for k in keys):
        raise ValueError(
            f"[{name}] 缺少 LLM 配置，请设置其中之一: {', '.join(keys)}"
        )


class RepoAuditAdapter(AgentAdapter):
    """RepoAudit — 基于 LLM 的符号执行 + 神经分析代码审计。"""

    name = "repoaudit"
    agent_dir = "repoaudit"
    src_root = "src"
    entry = ["repoaudit.py"]

    def build_cmd(self, target: str, kwargs: Dict[str, Any]) -> Tuple[List[str], Dict[str, str]]:
        _require_env(self.name, _LLM_ENV_HINTS[self.name])
        lang = str(kwargs.get("language", "Python"))
        cmd = [
            self.interpreter(),
            str(self.src_path() / "repoaudit.py"),
            "--project-path", target,
            "--language", lang,
        ]
        model = kwargs.get("model") or os.environ.get("REPOAUDIT_MODEL")
        if model:
            cmd += ["--model-name", str(model)]
        return cmd, self.env(target, kwargs)


class ArgusAdapter(AgentAdapter):
    """Argus — 开源安全扫描器（SAST/DAST/SCA/Secrets/IaC），无需 AI。"""

    name = "argus"
    agent_dir = "argus"
    src_root = "packages/python/src"

    def build_cmd(self, target: str, kwargs: Dict[str, Any]) -> Tuple[List[str], Dict[str, str]]:
        fmt = str(kwargs.get("format", "markdown"))
        scan_type = str(kwargs.get("scan_type", "all"))
        cmd = [
            self.interpreter(), "-m", "argus.cli", "scan", scan_type, target,
            "--format", fmt,
        ]
        return cmd, self.env(target, kwargs)

    def env(self, target: str, kwargs: Dict[str, Any]) -> Dict[str, str]:
        """注入 argus 主包与 argus_languages 内建扫描器两个源码根。"""
        env = dict(os.environ)
        roots = [
            str(self.src_path()),
            str(self.agent_path() / "packages" / "languages" / "src"),
        ]
        old_pypath = env.get("PYTHONPATH", "")
        joined = os.pathsep.join(roots)
        env["PYTHONPATH"] = joined if not old_pypath else (joined + os.pathsep + old_pypath)
        env.setdefault("HOS_AGENT", self.name)
        return env


class StrixAdapter(AgentAdapter):
    """Strix — 开源 AI 渗透测试 Agent（需 Docker 沙箱 + LLM）。"""

    name = "strix"
    agent_dir = "strix"
    src_root = ""

    def build_cmd(self, target: str, kwargs: Dict[str, Any]) -> Tuple[List[str], Dict[str, str]]:
        _require_env(self.name, _LLM_ENV_HINTS[self.name])
        mode = str(kwargs.get("scan_mode", "quick"))
        budget = int(kwargs.get("max_budget", 10))
        cmd = [
            self.interpreter(), "-m", "strix",
            "-n",                       # 非交互 headless
            "-t", target,
            "--scan-mode", mode,
            "--max-budget", str(budget),
        ]
        return cmd, self.env(target, kwargs)


class PentestGPTAdapter(AgentAdapter):
    """PentestGPT — AI 渗透测试 Agent（跑一次 trial，需 LLM）。"""

    name = "pentestgpt"
    agent_dir = "pentestgpt"
    src_root = "pentestgpt_agent/src"

    def build_cmd(self, target: str, kwargs: Dict[str, Any]) -> Tuple[List[str], Dict[str, str]]:
        _require_env(self.name, _LLM_ENV_HINTS[self.name])
        goal = str(kwargs.get("goal", f"audit {target}"))
        backend = str(kwargs.get("backend", "claude"))
        cmd = [
            self.interpreter(), "-m", "pentestgpt_agent.trial",
            "--goal", goal,
            "--target", target,
            "--backend", backend,
        ]
        return cmd, self.env(target, kwargs)


class DeepAuditAdapter(AgentAdapter):
    """DeepAudit — FastAPI 后端 AI 审计平台（服务式，通过 HTTP API 调用）。"""

    name = "deepaudit"
    agent_dir = "deepaudit"

    # 默认后端服务地址（需先启动 backend，见 agents/deepaudit 文档）
    DEFAULT_BASE_URL = os.environ.get("DEEPAUDIT_URL", "http://127.0.0.1:8000")

    def build_cmd(self, target: str, kwargs: Dict[str, Any]) -> Tuple[List[str], Dict[str, str]]:
        # 这里仅返回校验结果占位；真实扫描通过其 REST API 触发。
        base = self.DEFAULT_BASE_URL
        raise ValueError(
            f"[deepaudit] 需先启动后端服务（uvicorn backend.main:app），"
            f"然后通过 {base} 的 REST API 触发扫描（适配器暂以 API 集成）。"
        )


# ---------------------------------------------------------------------------
# 适配器注册中心
# ---------------------------------------------------------------------------
_AGENTS: Dict[str, AgentAdapter] = {
    cls.name: cls()
    for cls in (RepoAuditAdapter, DeepAuditAdapter, ArgusAdapter,
                StrixAdapter, PentestGPTAdapter)
}


def list_agents() -> List[str]:
    """列出所有已注册的安全 Agent 名称。"""
    return sorted(_AGENTS.keys())


def get_adapter(name: str) -> Optional[AgentAdapter]:
    """按名称获取适配器实例。"""
    return _AGENTS.get(name.lower())


def run_agent(name: str, target: str, **kwargs: Any) -> AdapterResult:
    """统一入口：执行指定安全 Agent（真实源码调用）。"""
    name = name.lower()
    adapter = _AGENTS.get(name)
    if adapter is None:
        return AdapterResult(
            agent=name, target=target, status="error",
            output=f"未知 Agent: {name}（可用: {list_agents()}）",
        )
    return adapter.run(target, **kwargs)


if __name__ == "__main__":
    print("已注册 Agent:", list_agents())
