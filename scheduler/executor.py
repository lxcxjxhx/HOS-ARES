# -*- coding: utf-8 -*-
"""
scheduler/executor.py — 调度执行器

提供：
    - Scheduler 类：run(task_spec, task_input) -> dict
        1. 按 workflow 顺序遍历步骤（如 analyze → verify → report）
        2. 每个步骤调用对应 agent（通过 skills registry / tool executor，此处为占位）
        3. 汇总为最终报告 dict（含每个步骤的结果）

说明：
    真实环境中，各步骤会通过 skills registry 或 tool executor 调用对应
    Agent 的技能插件（如 repoaudit / deepaudit / argus）。当前脚手架以
    占位实现返回模拟结果，保证流程可运行验证。
"""
from __future__ import annotations

from typing import Any, Dict, List

from .config import load_config
from .schema import TaskSpec, WorkflowStep


class Scheduler:
    """
    Agent 调度执行器。

    按 TaskSpec.workflow 中的步骤顺序执行，并将每个步骤的结果
    汇聚到最终报告 dict 中。
    """

    def __init__(self, skills_registry: Any = None) -> None:
        """
        参数：
            skills_registry: 技能/工具注册表（预留）。
                真实实现可注入一个可调用对象，按 agent 名执行对应技能；
                默认 None 时使用占位执行器。
        """
        self.skills_registry = skills_registry

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def run(self, task_spec: TaskSpec, task_input: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行调度：按 workflow 顺序调用各步骤对应 agent，汇总报告。

        参数：
            task_spec:  任务规格（type / agents / workflow）
            task_input: 任务输入（如待审计的项目路径、上下文等）

        返回：
            最终报告 dict，包含：
                - task_type  任务类型
                - agents     参与的 Agent
                - workflow   步骤名顺序
                - steps      每个步骤的结果列表
                - summary    汇总摘要
        """
        steps: List[Dict[str, Any]] = []

        # 1) 按 workflow 顺序遍历步骤
        for step in (task_spec.workflow or []):
            result = self._execute_step(step, task_spec, task_input)
            steps.append(result)

        # 2) 汇总最终报告
        report = {
            "task_type": task_spec.type,
            "agents": list(task_spec.agents),
            "workflow": task_spec.step_names,
            "steps": steps,
            "summary": self._compose_summary(task_spec, steps),
        }
        return report

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------
    def _execute_step(
        self,
        step: WorkflowStep,
        task_spec: TaskSpec,
        task_input: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        执行单个工作流步骤。

        1. 若有 skills_registry，则调用其执行对应 agent 技能（占位约定）；
        2. 否则调用占位执行器 _placeholder_run_agent。
        """
        if self.skills_registry is not None:
            agent_output = self.skills_registry.run_agent(step.agent, step.name, task_input)
        else:
            agent_output = self._placeholder_run_agent(step.agent, step.name, task_input)

        return {
            "step": step.name,
            "agent": step.agent,
            "output": agent_output,
        }

    @staticmethod
    def _placeholder_run_agent(
        agent: str, step_name: str, task_input: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        【占位】模拟调用 agent 返回结果。

        真实实现说明：
            此处将通过 skills registry / tool executor 调用对应技能，
            例如：repoaudit scan <path>、deepaudit analyze、argus review。
        """
        target = str(task_input.get("target", "<未指定>"))
        return {
            "status": "ok",
            "agent": agent,
            "step": step_name,
            "message": f"[占位] Agent「{agent}」执行步骤「{step_name}」，目标：{target}",
        }

    @staticmethod
    def _compose_summary(task_spec: TaskSpec, steps: List[Dict[str, Any]]) -> str:
        """生成最终报告摘要（占位）。"""
        seq = " → ".join(task_spec.step_names) or "无"
        agents = ", ".join(task_spec.agents) or "无"
        ok_count = sum(1 for s in steps if (s.get("output") or {}).get("status") == "ok")
        return (
            f"任务类型「{task_spec.type}」执行完成："
            f"涉及 Agent【{agents}】，流程：{seq}，成功步骤 {ok_count}/{len(steps)}"
        )


def main() -> None:
    """
    可运行验证示例：

        python -m scheduler.executor

    步骤：
        1. 加载 configs/security_audit.yaml 为 TaskSpec；
        2. 用 Scheduler 执行 analyze → verify → report；
        3. 打印各步骤结果与最终报告。
    """
    import os

    base = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(os.path.dirname(base), "configs", "security_audit.yaml")

    # 1) 加载配置
    spec = load_config(config_path)
    print("=" * 60)
    print(f"[配置] 任务类型    : {spec.type}")
    print(f"[配置] Agents      : {spec.agents}")
    print(f"[配置] Workflow    : {spec.step_names}")
    print("=" * 60)

    # 2) 执行调度
    scheduler = Scheduler()
    task_input = {"target": "./project", "task_type": spec.type}
    report = scheduler.run(spec, task_input)

    # 3) 输出各步骤结果
    print("\n[执行] 各步骤结果：")
    for step in report["steps"]:
        out = step["output"]
        print(f"  - 步骤「{step['step']}」→ Agent「{step['agent']}」: {out['message']}")

    # 4) 输出最终报告
    print("\n[报告] 最终报告：")
    print(f"  summary : {report['summary']}")
    print(f"  workflow: {report['workflow']}")
    print("  steps   : 共 %d 个步骤" % len(report["steps"]))
    print("=" * 60)


if __name__ == "__main__":
    main()
