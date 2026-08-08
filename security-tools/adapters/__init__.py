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

import json
import os
import urllib.parse
import urllib.request
from pathlib import Path
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
    """DeepAudit — FastAPI 后端 AI 审计平台（服务式，通过 REST API 同步扫描）。"""

    name = "deepaudit"
    agent_dir = "deepaudit"

    # 文本扩展名 → DeepAudit 识别的语言名（与 backend get_language_from_path 近似）
    EXT_LANG: Dict[str, str] = {
        ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript",
        ".jsx": "JavaScript", ".java": "Java", ".go": "Go", ".rs": "Rust",
        ".cpp": "C++", ".c": "C", ".h": "C++", ".cc": "C++", ".cs": "C#",
        ".php": "PHP", ".rb": "Ruby", ".kt": "Kotlin", ".swift": "Swift",
        ".sql": "SQL", ".sh": "Shell", ".json": "JSON", ".yml": "YAML", ".yaml": "YAML",
    }
    MAX_FILE_BYTES = int(os.environ.get("DEEPAUDIT_MAX_FILE_BYTES", "1048576"))  # 1MB
    DEFAULT_MAX_FILES = int(os.environ.get("DEEPAUDIT_MAX_FILES", "50"))

    def __init__(self, timeout: int = 600) -> None:
        super().__init__(timeout)
        self.base_url = (os.environ.get("DEEPAUDIT_URL") or "http://127.0.0.1:8000").rstrip("/")
        self.token = os.environ.get("DEEPAUDIT_TOKEN") or ""
        self.email = os.environ.get("DEEPAUDIT_EMAIL")
        self.password = os.environ.get("DEEPAUDIT_PASSWORD")

    def build_cmd(self, target: str, kwargs: Dict[str, Any]) -> Tuple[List[str], Dict[str, str]]:
        # REST 集成，不使用 subprocess
        raise NotImplementedError(
            "[deepaudit] 适配器通过 REST API 集成，不走命令行；请直接调用 run()。"
        )

    def run(self, target: str, **kwargs: Any) -> AdapterResult:
        if not (self.base_url and self.base_url.startswith("http")):
            return AdapterResult(
                self.name, target, "error",
                output=f"[deepaudit] 未配置有效 DEEPAUDIT_URL: {self.base_url!r}",
            )
        try:
            token = self._get_token()
        except Exception as exc:
            return AdapterResult(
                self.name, target, "error",
                output=f"[deepaudit] 获取 token 失败: {exc}",
            )

        files = self._collect_files(target, kwargs)
        if not files:
            return AdapterResult(
                self.name, target, "not_available",
                output=f"[deepaudit] 目标目录无代码文件或不可读: {target}",
            )

        findings: List[str] = []
        lines: List[str] = []
        total_issues = 0
        failed = 0
        for rel, lang, content in files:
            try:
                issues = self._instant(token, content, lang)
            except Exception as exc:
                failed += 1
                lines.append(f"[deepaudit] 分析 {rel} 失败: {exc}")
                continue
            total_issues += len(issues)
            lines.append(f"[deepaudit] {rel} ({lang}): {len(issues)} issues")
            for it in issues:
                sev = str(it.get("severity", "low"))
                title = str(it.get("title", it.get("message", "")))
                findings.append(f"[{rel}] {sev}: {title}")

        status = "error" if (files and failed == len(files)) else "ok"
        header = f"[deepaudit] 扫描完成：{len(files)} 文件，{total_issues} issues，{failed} 失败"
        return AdapterResult(
            self.name, target, status,
            output="\n".join([header] + lines),
            findings=findings,
        )

    # ------------------------------------------------------------------
    # 内部 REST 辅助
    # ------------------------------------------------------------------
    def _get_token(self) -> str:
        if self.token:
            return self.token
        if not (self.email and self.password):
            raise ValueError(
                "未配置 DEEPAUDIT_TOKEN 或 DEEPAUDIT_EMAIL/DEEPAUDIT_PASSWORD，"
                "无法调用需认证的 DeepAudit API。"
            )
        url = f"{self.base_url}/api/v1/auth/login"
        data = urllib.parse.urlencode(
            {"username": self.email, "password": self.password}
        ).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        token = payload.get("access_token")
        if not token:
            raise ValueError(f"登录响应缺少 access_token: {payload}")
        return token

    def _instant(self, token: str, code: str, language: str) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/api/v1/scan/instant"
        body = json.dumps({"code": code, "language": language}).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return payload.get("issues", []) or []

    def _collect_files(self, target: str, kwargs: Dict[str, Any]) -> List[Tuple[str, str, str]]:
        root = Path(target)
        if not root.exists() or not root.is_dir():
            return []
        max_files = int(kwargs.get("max_files") or self.DEFAULT_MAX_FILES)
        out: List[Tuple[str, str, str]] = []
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            lang = self.EXT_LANG.get(p.suffix.lower())
            if lang is None:
                continue
            try:
                content = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if len(content.encode("utf-8", errors="ignore")) > self.MAX_FILE_BYTES:
                continue
            out.append((p.relative_to(root).as_posix(), lang, content))
            if len(out) >= max_files:
                break
        return out


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
