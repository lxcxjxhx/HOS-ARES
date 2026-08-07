# -*- coding: utf-8 -*-
"""
scheduler/config.py — 调度配置加载器

提供：
    - load_config(path) -> TaskSpec ：解析 YAML 配置文件为 TaskSpec
    - default_security_audit() -> TaskSpec ：内置默认 security_audit 配置

YAML 解析说明：
    优先使用 PyYAML（import yaml）；若未安装，则优雅降级：
        1. 尝试以 JSON 解析（配置文件为 JSON 时仍可用）；
        2. 若 JSON 也失败，回退到内置默认配置，并打印提示，不因缺依赖而报错。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from .schema import TaskSpec, WorkflowDef, WorkflowStep


# ---------------------------------------------------------------------------
# 内置默认配置：security_audit（analyze → verify → report）
# ---------------------------------------------------------------------------
DEFAULT_SECURITY_AUDIT: Dict[str, Any] = {
    "task": {
        "type": "security_audit",
        "agents": ["argus", "strix", "repoaudit", "deepaudit", "pentestgpt"],
        "workflow": ["analyze", "verify", "report"],
    },
}


def default_security_audit() -> TaskSpec:
    """返回内置默认的 security_audit 配置（TaskSpec）。"""
    return build_task_spec(DEFAULT_SECURITY_AUDIT)


# ---------------------------------------------------------------------------
# 解析与构造
# ---------------------------------------------------------------------------
def _try_import_yaml():
    """尝试导入 PyYAML；失败返回 None。"""
    try:
        import yaml  # type: ignore
        return yaml
    except ImportError:
        return None


def _load_raw_config(path: str) -> Dict[str, Any]:
    """
    从文件加载原始配置 dict。

    优先 PyYAML；缺失时尝试 JSON；再失败则抛异常由上层处理。
    """
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()

    yaml_mod = _try_import_yaml()
    if yaml_mod is not None:
        # 使用 safe_load 避免任意对象反序列化
        data = yaml_mod.safe_load(text)
        if isinstance(data, dict):
            return data
        raise ValueError(f"配置文件根节点不是字典：{path}")

    # ---- PyYAML 未安装：优雅降级路径 ----
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
        raise ValueError(f"配置文件根节点不是字典：{path}")
    except json.JSONDecodeError:
        # 既非 YAML 也非 JSON，无法解析 → 抛错交由上层回退默认配置
        raise ValueError(f"无法解析配置文件（PyYAML 未安装且非 JSON 格式）：{path}")


def _normalize_workflow(
    raw_workflow: Any, agents: List[str]
) -> WorkflowDef:
    """
    将 workflow 字段规范化为 WorkflowDef。

    支持两种形态：
        1. 纯列表：["analyze", "verify", "report"]
           步骤按顺序对应到 agents（第 i 步由 agents[i] 执行，越界回退到第一个 agent）
        2. 步骤 dict 列表：[{"name": "analyze", "agent": "repoaudit"}, ...]
    """
    if raw_workflow is None:
        return WorkflowDef()

    steps: List[WorkflowStep] = []

    if isinstance(raw_workflow, list):
        for idx, item in enumerate(raw_workflow):
            if isinstance(item, dict):
                name = str(item.get("name", ""))
                agent = str(item.get("agent", "")) or _agent_for_index(agents, idx)
                params = dict(item.get("params", {}) or {})
                steps.append(WorkflowStep(name=name, agent=agent, params=params))
            else:
                # 纯名称字符串，按索引映射到 agent
                name = str(item)
                agent = _agent_for_index(agents, idx)
                steps.append(WorkflowStep(name=name, agent=agent))
    else:
        raise ValueError(f"workflow 字段应为列表，得到：{type(raw_workflow)}")

    return WorkflowDef(steps=steps)


def _agent_for_index(agents: List[str], idx: int) -> str:
    """按索引取 agent，越界时回退到第一个 agent。"""
    if not agents:
        return ""
    return agents[idx] if idx < len(agents) else agents[0]


def build_task_spec(data: Dict[str, Any]) -> TaskSpec:
    """
    从原始配置 dict 构造 TaskSpec。

    支持的配置结构：
        {
            "task": {"type": "security_audit"},
            "agents": ["repoaudit", "deepaudit", "argus"],
            "workflow": ["analyze", "verify", "report"],
        }
    """
    task = data.get("task", {}) or {}
    task_type = str(task.get("type", ""))
    # agents 兼容两种写法：顶层 `agents:` 或嵌套在 `task.agents:`
    agents_raw = data.get("agents")
    if not agents_raw:
        agents_raw = task.get("agents")
    agents = [str(a) for a in (agents_raw or [])]
    # workflow 兼容两种写法：顶层 `workflow:` 或嵌套在 `task.workflow:`
    workflow_raw = data.get("workflow")
    if workflow_raw is None:
        workflow_raw = task.get("workflow")
    workflow = _normalize_workflow(workflow_raw, agents)
    meta = dict(task.get("meta", {}) or {}) or {}
    return TaskSpec(type=task_type, agents=agents, workflow=workflow, meta=meta)


def load_config(path: str) -> TaskSpec:
    """
    加载调度配置文件并返回 TaskSpec。

    参数：
        path: 配置文件路径（YAML 或 JSON）。

    返回：
        TaskSpec。

    说明：
        若文件不存在、解析失败，会回退到内置默认 security_audit 配置，
        并打印提示信息，保证脚手架可用、不因缺依赖而崩溃。
    """
    if not os.path.exists(path):
        print(f"[config] 配置文件不存在，回退默认 security_audit 配置：{path}")
        return default_security_audit()

    try:
        raw = _load_raw_config(path)
        return build_task_spec(raw)
    except Exception as exc:  # 解析失败时优雅降级
        print(f"[config] 解析配置失败（{exc}），回退默认 security_audit 配置：{path}")
        return default_security_audit()
