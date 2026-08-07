# -*- coding: utf-8 -*-
"""
gateway/task_recognizer.py — 任务识别器（TaskRecognizer）

职责：
    将用户的自然语言任务解析为可调度的「任务类型」。

脚手架阶段：
    基于关键词/规则做简单的任务识别（如包含「审计 / audit / vulnerability / 漏洞」→ security_audit）。

后续：
    预留 LLM 识别接口（recognize_with_llm），由大模型做更灵活的意图分类，
    以覆盖规则无法穷尽的表达方式。
"""
from __future__ import annotations

from typing import Dict, Optional


# 任务类型常量（后续调度链依据该类型选择 workflow）
TASK_SECURITY_AUDIT = "security_audit"  # 安全审计类任务
TASK_GENERAL = "general"                # 通用/默认任务


class TaskRecognizer:
    """基于关键词/规则的任务识别器。"""

    # 规则：任务类型 -> 触发关键词列表（小写匹配）
    # 命中任一关键词即认为属于该任务类型。
    RULES: Dict[str, tuple] = {
        TASK_SECURITY_AUDIT: (
            "审计",
            "audit",
            "vulnerability",
            "漏洞",
            "安全",
            "security",
            "扫描",
            "scan",
        ),
    }

    def __init__(self) -> None:
        # 默认任务类型：规则未命中时的兜底
        self.default_type: str = TASK_GENERAL

    def recognize(self, task: str) -> str:
        """
        识别任务类型。

        参数：
            task: 用户提交的自然语言任务。

        返回：
            任务类型字符串（见模块级 TASK_* 常量）。
        """
        if not task or not task.strip():
            return self.default_type

        lowered = task.lower()
        for task_type, keywords in self.RULES.items():
            for keyword in keywords:
                if keyword in lowered:
                    return task_type
        return self.default_type

    # ------------------------------------------------------------------
    # 预留：LLM 驱动的任务识别接口
    # ------------------------------------------------------------------
    def recognize_with_llm(self, task: str) -> Optional[Dict]:
        """
        【占位】基于 LLM 的任务识别。

        真实实现建议：
            1. 将 task 与任务类型枚举（TASK_*）一起发给大模型；
            2. 请求模型返回结构化结果（如 JSON：{"task_type": "...", "reason": "..."}）；
            3. 解析并返回识别结果 dict，未命中时返回 None 回退到规则识别。

        参数：
            task: 用户提交的自然语言任务。

        返回：
            dict（含 task_type / reason 等字段）或 None。
        """
        # TODO(脚手架): 接入大模型（如通过 ReasonixAgent 或直接调用 LLM API）
        # 例：
        #   prompt = f"请识别以下任务的类型，可选类型：{list(self.RULES.keys())}\n任务：{task}"
        #   resp = llm_client.complete(prompt)
        #   return parse_response(resp)
        return None
