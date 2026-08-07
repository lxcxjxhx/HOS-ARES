# -*- coding: utf-8 -*-
"""
scheduler/schema.py — 调度配置 schema 定义

定义 Agent 调度所需的数据结构：
    - TaskSpec       任务规格（task type、参与 agents、workflow）
    - WorkflowStep   工作流中的单个步骤（步骤名 + 执行该步骤的 agent）
    - WorkflowDef    工作流定义（按顺序排列的步骤列表）

对应 PLAN.MD 中的声明式调度配置：
    task:
      type: security_audit
    agents:
      - repoaudit
      - deepaudit
      - argus
    workflow:
      - analyze
      - verify
      - report
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class WorkflowStep:
    """
    工作流中的单个执行步骤。

    字段：
        name:   步骤名称（如 analyze / verify / report）
        agent:  执行该步骤的 Agent 名称（如 repoaudit / deepaudit / argus）
        params: 该步骤的附加参数（可选，脚手架预留）
    """
    name: str
    agent: str = ""
    params: Dict[str, object] = field(default_factory=dict)


@dataclass
class WorkflowDef:
    """
    工作流定义：按顺序排列的步骤列表。

    字段：
        steps: 依次执行的步骤列表
    """
    steps: List[WorkflowStep] = field(default_factory=list)

    @property
    def names(self) -> List[str]:
        """返回所有步骤名称（如 ["analyze", "verify", "report"]）。"""
        return [step.name for step in self.steps]

    def __len__(self) -> int:
        return len(self.steps)

    def __iter__(self):
        return iter(self.steps)


@dataclass
class TaskSpec:
    """
    任务规格：描述一个 Agent 调度任务。

    字段：
        type:     任务类型（如 security_audit）
        agents:   参与调度的 Agent 列表（如 ["repoaudit", "deepaudit", "argus"]）
        workflow: 工作流定义（步骤的有序集合）
        meta:     附加元信息（可选，脚手架预留）
    """
    type: str
    agents: List[str] = field(default_factory=list)
    workflow: Optional[WorkflowDef] = None
    meta: Dict[str, object] = field(default_factory=dict)

    # 便捷访问：默认 worklow 为空时返回空列表
    @property
    def step_names(self) -> List[str]:
        """返回工作流步骤名列表。"""
        if self.workflow is None:
            return []
        return self.workflow.names
