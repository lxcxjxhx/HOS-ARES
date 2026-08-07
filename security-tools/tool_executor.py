# -*- coding: utf-8 -*-
"""
security-tools/tool_executor.py — Security Tool Layer 执行器

职责：
    - 统一封装底层安全 Agent（repoaudit / deepaudit / argus / strix / pentestgpt）
    - 提供 exec(tool_name, target, **kwargs) -> ToolResult 的统一调用接口
    - 是 Agent / 技能层与具体安全 Agent 源码之间的薄封装层

实现：
    底层通过 security-tools/adapters 调用从 GitHub 拉取的真实源码
    （agents/<name>），不再是 PATH 占位。
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    from .adapters import run_agent, list_agents
except ImportError:  # 以脚本方式直接运行时，回退到绝对路径导入
    _here = os.path.dirname(os.path.abspath(__file__))
    if _here not in sys.path:
        sys.path.insert(0, _here)
    from adapters import run_agent, list_agents


# 各工具对应的默认扫描动词（供无参调用时兜底）
TOOL_DEFAULT_VERB: Dict[str, str] = {
    "repoaudit": "scan",
    "deepaudit": "analyze",
    "argus": "review",
    "strix": "scan",
    "pentestgpt": "audit",
}


@dataclass
class ToolResult:
    """工具执行结果的数据结构。"""

    tool: str                          # 工具名（repoaudit / deepaudit / argus）
    target: str                        # 目标路径/仓库
    status: str                        # ok / error / not_found
    output: str = ""                   # 命令原始输出（stdout + stderr）
    returncode: Optional[int] = None   # 子进程返回码（未执行时为 None）
    findings: List[str] = field(default_factory=list)  # 解析出的结构化发现项

    @property
    def ok(self) -> bool:
        """是否执行成功。"""
        return self.status == "ok"


class ToolExecutor:
    """安全工具执行器：根据工具名调用对应 CLI 命令。"""

    def __init__(self, timeout: int = 120) -> None:
        self.timeout: int = timeout  # 子进程超时秒数

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------
    def exec(self, tool_name: str, target: str, **kwargs: Any) -> ToolResult:
        """
        执行指定安全 Agent（真实源码调用）。

        参数：
            tool_name: Agent 名（repoaudit / deepaudit / argus / strix / pentestgpt）。
            target:    目标路径 / 仓库 / URL。
            **kwargs:  透传给底层适配器的额外参数（language / scan_mode / goal...）。

        返回：
            ToolResult 数据结构。
        """
        tool_name = tool_name.lower()
        result = run_agent(tool_name, target, timeout=self.timeout, **kwargs)
        return ToolResult(
            tool=result.agent,
            target=result.target,
            status=result.status,
            output=result.output,
            returncode=result.returncode,
            findings=result.findings,
        )


if __name__ == "__main__":
    # 验证示例：列出可用 Agent 并执行一次（无 LLM 时返回错误/提示）
    exec = ToolExecutor()
    print("=" * 50)
    print("可用 Agent:", list_agents())
    print("=" * 50)
    for tool in ("argus", "repoaudit", "strix", "pentestgpt", "deepaudit", "unknown"):
        r = exec.exec(tool, "./project")
        print(f"[{r.tool}] status={r.status} returncode={r.returncode}")
        if r.output:
            print(f"    output: {r.output[:300]}")
        print("-" * 50)
