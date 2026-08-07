# -*- coding: utf-8 -*-
"""
gateway/agent_gateway.py — AI Agent Gateway 统一入口

职责：
    作为 HOS-ARES 面向用户的统一 Agent 入口：
        1. 接收用户自然语言任务（submit）
        2. 调用 TaskRecognizer 判断任务类型
        3. 将任务交给 ReasonixAgent（统一 Agent OS）调度执行
        4. 汇聚调度结果与报告，封装为 TaskResult 返回

本文件为脚手架：
    submit() 为可验证的占位实现；真实环境中会调用 Reasonix Agent Runtime
    （见 agents/reasonix/agent.py 的 ReasonixAgent 封装）。

流程：任务识别 → 调度 → 工具调用 → 报告。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from .task_recognizer import TaskRecognizer, TASK_SECURITY_AUDIT


class TaskStatus(str, Enum):
    """任务状态。"""
    PENDING = "pending"      # 待调度
    RUNNING = "running"      # 调度/执行中
    SUCCEEDED = "succeeded"  # 成功
    FAILED = "failed"        # 失败


@dataclass
class TaskResult:
    """
    任务调度结果数据结构。

    字段：
        task_id:   任务唯一 ID
        task:      原始用户任务文本
        task_type: 识别出的任务类型（如 security_audit）
        agents:    调度涉及的 Agent 列表（如 ["repoaudit", "deepaudit", "argus"]）
        workflow:  调度链 / 工作流描述
        status:    任务状态（TaskStatus）
        report:    汇聚后的审计/执行报告（占位，真实环境由各 Agent 输出汇聚）
        error:     失败时的错误信息（可选）
    """
    task_id: str
    task: str
    task_type: str
    agents: List[str] = field(default_factory=list)
    workflow: str = ""
    status: TaskStatus = TaskStatus.PENDING
    report: Optional[str] = None
    error: Optional[str] = None


class AgentGateway:
    """AI Agent Gateway：统一 Agent 入口。"""

    # 任务类型 -> 默认调度链（Agent 技能插件顺序）
    # 以「审计这个项目」为例：RepoAudit → DeepAudit → Argus
    WORKFLOWS = {
        TASK_SECURITY_AUDIT: {
            "agents": ["repoaudit", "deepaudit", "argus"],
            "workflow": "RepoAudit → DeepAudit → Argus",
        },
    }

    def __init__(self) -> None:
        # 任务识别器
        self.recognizer: TaskRecognizer = TaskRecognizer()
        # 预留：Reasonix Agent 实例（真实实现中在此注入）
        # from ..agents.reasonix.agent import ReasonixAgent
        # self.reasonix = ReasonixAgent(config=...)
        self.reasonix = None

    # ------------------------------------------------------------------
    # 对外统一入口
    # ------------------------------------------------------------------
    def submit(self, task: str) -> TaskResult:
        """
        接收用户自然语言任务，识别任务类型并调度，返回 TaskResult。

        参数：
            task: 用户提交的自然语言任务（如「审计这个项目」）。

        返回：
            TaskResult 调度结果。
        """
        # 1) 生成任务 ID
        task_id = self._new_task_id()

        # 2) 任务识别：先走规则；真实环境可优先 LLM（recognize_with_llm）
        task_type = self.recognizer.recognize(task)

        # 3) 根据任务类型确定调度链
        chain = self.WORKFLOWS.get(task_type, {})
        agents = list(chain.get("agents", []))
        workflow = chain.get("workflow", "直接处理")

        result = TaskResult(
            task_id=task_id,
            task=task,
            task_type=task_type,
            agents=agents,
            workflow=workflow,
        )

        # 4) 调度执行（脚手架：占位）
        try:
            self._dispatch(task, task_type, agents)
            result.status = TaskStatus.SUCCEEDED
            result.report = self._compose_report(result)
        except Exception as exc:  # 脚手架：吞掉异常并标记失败
            result.status = TaskStatus.FAILED
            result.error = str(exc)

        return result

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------
    def _dispatch(self, task: str, task_type: str, agents: List[str]) -> None:
        """
        【占位】将任务交给 Agent 调度执行。

        真实实现说明：
            这里会调用 Reasonix Agent Runtime（第一选择 Agent OS）完成
            任务理解、Agent 调度与工具调用。典型步骤：
              1. 构造 Reasonix 任务请求（task、task_type、agents 调度链）；
              2. 调用 ReasonixAgent.run(task, config) 或 reasonix CLI/API；
              3. Reasonix 调度 RepoAudit → DeepAudit → Argus 依次执行；
              4. 汇聚各 Agent 输出。
        """
        if self.reasonix is None:
            # 脚手架：未注入 ReasonixAgent 时仅记录占位调度结果，不抛错。
            # TODO(脚手架): 真实实现在此调用 Reasonix Agent Runtime
            #   result = self.reasonix.run(task, config=self._build_config(task_type, agents))
            return
        # 真实实现路径（占位）
        # result = self.reasonix.run(task, config=self._build_config(task_type, agents))
        # 将 result 写入 result.report / result.status 等
        raise NotImplementedError("ReasonixAgent 真实调度逻辑待实现")

    def _build_config(self, task_type: str, agents: List[str]) -> dict:
        """构造传给 ReasonixAgent 的调度配置（占位）。"""
        return {
            "task_type": task_type,
            "agents": agents,
            # 真实实现可补充：模型、环境、工具等配置
        }

    def _compose_report(self, result: TaskResult) -> str:
        """汇聚调度结果生成简要报告（占位）。"""
        return (
            f"任务[{result.task_id}] 识别为「{result.task_type}」，"
            f"调度链：{result.workflow}，涉及 Agent：{', '.join(result.agents) or '无'}"
        )

    @staticmethod
    def _new_task_id() -> str:
        """生成任务唯一 ID。"""
        return uuid.uuid4().hex[:12]


if __name__ == "__main__":
    # 简单的可验证示例：
    #   python -m gateway.agent_gateway
    gw = AgentGateway()
    for sample in ["审计这个项目", "帮我分析一下这里的漏洞", "随便聊聊"]:
        r = gw.submit(sample)
        print(f"[{r.task_type}] {r.task} -> {r.status.value} | {r.report}")
