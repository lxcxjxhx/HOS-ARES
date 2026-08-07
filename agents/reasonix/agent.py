# -*- coding: utf-8 -*-
"""
agents/reasonix/agent.py — ReasonixAgent 封装类

职责：
    封装 Reasonix Agent Runtime 的调用，作为统一 Agent OS 的入口封装。
    由 gateway/AgentGateway 调度使用。

本文件为脚手架：
    run() 为占位实现，说明真实环境中调用 reasonix CLI/API 的步骤；
    包含默认模型/环境配置字段（从环境变量读取，如 HOS_ARES_REASONIX_MODEL、HOS_ARES_API_KEY）；
    预留「审计这个项目」示例任务的识别与调度逻辑。
"""
from __future__ import annotations

import os
from typing import Dict, Optional


class ReasonixAgent:
    """
    Reasonix Agent 封装类。

    属性：
        model:    默认模型（从 HOS_ARES_REASONIX_MODEL 读取）
        api_key:  大模型 API Key（从 HOS_ARES_API_KEY 读取）
        config:   运行配置 dict（可传入，或读取 config.yaml）
    """

    # 默认模型（环境变量未设置时的兜底）
    DEFAULT_MODEL = "reasonix-default"

    def __init__(self, config: Optional[Dict] = None) -> None:
        # 从环境变量读取默认模型与 API Key
        self.model: str = os.getenv("HOS_ARES_REASONIX_MODEL", self.DEFAULT_MODEL)
        self.api_key: str = os.getenv("HOS_ARES_API_KEY", "") or ""
        # 运行配置：外部传入优先，否则使用默认
        self.config: Dict = config or self._load_default_config()

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def run(self, task: str, config: Optional[Dict] = None) -> Dict:
        """
        【占位】运行 Reasonix 任务，返回执行结果 dict。

        参数：
            task:   任务文本（如「审计这个项目」）。
            config: 可选，本次运行覆盖配置。

        返回：
            dict，包含 status / task_type / agents / report 等字段。
        """
        cfg = {**self.config, **(config or {})}

        # 真实环境调用 reasonix CLI / API 的步骤（占位注释）：
        #
        #   1. 组装请求负载：
        #      payload = {
        #          "model": cfg.get("model", self.model),
        #          "task": task,
        #          "tools": cfg.get("tools", []),
        #          "env": cfg.get("env", {}),
        #      }
        #   2. 调用 reasonix API（本地服务，Android 内 http://127.0.0.1:{port}/...）：
        #      resp = requests.post(f"{base_url}/agent/run", json=payload,
        #                           headers={"Authorization": f"Bearer {self.api_key}"})
        #   3. 或调用 reasonix CLI：
        #      subprocess.run(["reasonix", "run", "--task", task, ...])
        #   4. 解析响应，汇聚各安全 Agent（RepoAudit / DeepAudit / Argus）输出。
        #
        # TODO(脚手架): 对接真实 Reasonix Agent Runtime

        # 占位结果：返回一次「审计这个项目」示例任务的调度链
        return self._placeholder_result(task, cfg)

    # ------------------------------------------------------------------
    # 「审计这个项目」示例任务：识别与调度逻辑（占位）
    # ------------------------------------------------------------------
    def handle_audit_project(self, task: str, cfg: Dict) -> Dict:
        """
        示例：处理「审计这个项目」类任务。

        真实实现中，此方法会：
            1. 调用 gateway 的 TaskRecognizer 识别任务类型（security_audit）；
            2. 走 AgentGateway 调度链，交由 Reasonix Agent Runtime 执行：
               RepoAudit → DeepAudit → Argus；
            3. 汇聚结果为报告。

        调用链示意（真实实现）：
            from gateway.agent_gateway import AgentGateway
            gw = AgentGateway()
            result = gw.submit(task)
            # result.task_type == "security_audit"
            # result.agents == ["repoaudit", "deepaudit", "argus"]
        """
        # 脚手架：直接返回预设的调度链信息
        return {
            "status": "ok",
            "task": task,
            "task_type": "security_audit",
            "agents": ["repoaudit", "deepaudit", "argus"],
            "workflow": "RepoAudit → DeepAudit → Argus",
            "note": "占位实现：真实环境由 Reasonix Agent Runtime 调度执行",
        }

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------
    def _placeholder_result(self, task: str, cfg: Dict) -> Dict:
        """占位执行结果：演示「审计这个项目」示例任务的识别与调度。"""
        # 简易任务类型判断（占位）：含审计/漏洞关键词则走审计调度链
        if any(k in task.lower() for k in ("审计", "audit", "漏洞", "vulnerability")):
            return self.handle_audit_project(task, cfg)

        return {
            "status": "ok",
            "task": task,
            "task_type": "general",
            "agents": [],
            "workflow": "直接处理",
            "note": "占位实现：真实环境由 Reasonix Agent Runtime 处理",
        }

    # ------------------------------------------------------------------
    # 配置加载
    # ------------------------------------------------------------------
    @staticmethod
    def _load_default_config() -> Dict:
        """
        加载默认配置。

        真实实现建议读取 config.yaml（与默认字段对齐）：
            import yaml
            with open(os.path.join(os.path.dirname(__file__), "config.yaml")) as f:
                return yaml.safe_load(f)

        TODO(脚手架): 接入 yaml 加载
        """
        return {
            "model": os.getenv("HOS_ARES_REASONIX_MODEL", ReasonixAgent.DEFAULT_MODEL),
            "env": {
                "API_KEY": os.getenv("HOS_ARES_API_KEY", ""),
                "REASONIX_PORT": os.getenv("REASONIX_PORT", "8080"),
            },
            "tools": ["repoaudit", "deepaudit", "argus"],
        }


if __name__ == "__main__":
    # 简单的可验证示例：
    #   python -m agents.reasonix.agent
    agent = ReasonixAgent()
    for sample in ["审计这个项目", "今天天气如何"]:
        print(agent.run(sample))
