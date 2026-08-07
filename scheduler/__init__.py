# -*- coding: utf-8 -*-
"""scheduler — Agent 调度配置模块。

包含：
    schema.py   —— 调度配置数据结构（TaskSpec / WorkflowDef / WorkflowStep）
    config.py   —— 配置加载器（load_config，YAML → TaskSpec）
    executor.py —— 调度执行器（Scheduler，按 workflow 顺序执行各步骤）

参考 PLAN.MD 中的声明式调度配置。
"""
