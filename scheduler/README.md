# HOS-ARES Agent 调度模块（scheduler）

Agent 调度配置模块，用于描述 **Agent 调度 schema**：任务类型（task type）、
参与 Agent 与工作流顺序（workflow）。参考 `PLAN.MD` 中的声明式调度配置。

## 目录结构

```
scheduler/
├── __init__.py     # 包说明
├── schema.py       # 调度配置 schema（TaskSpec / WorkflowDef / WorkflowStep）
├── config.py       # 配置加载器（load_config，YAML → TaskSpec）
├── executor.py     # 调度执行器（Scheduler，按 workflow 顺序执行）
└── __main__.py     # 命令行入口（python -m scheduler）
configs/
└── security_audit.yaml   # 示例配置：安全审计
```

## 调度 schema

调度配置描述三件事：**task type**、**agents**、**workflow**。

| 概念      | 数据结构         | 说明                                            |
| --------- | ---------------- | ----------------------------------------------- |
| 任务规格   | `TaskSpec`       | `type`（任务类型）、`agents`（Agent 列表）、`workflow` |
| 工作流     | `WorkflowDef`    | 按顺序排列的步骤列表                             |
| 工作流步骤 | `WorkflowStep`   | `name`（步骤名）、`agent`（执行该步骤的 Agent）  |

示例：

```yaml
task:
  type: security_audit          # 任务类型
  agents:                       # 参与调度的 Agent
    - repoaudit
    - deepaudit
    - argus
workflow:                       # 工作流顺序
  - analyze    # 由 repoaudit 执行
  - verify     # 由 deepaudit 执行
  - report     # 由 argus 执行
```

`workflow` 也支持显式指定每个步骤的 agent（dict 形式）：

```yaml
workflow:
  - name: analyze
    agent: repoaudit
  - name: verify
    agent: deepaudit
  - name: report
    agent: argus
```

## 配置格式

- 默认使用 **YAML**（PyYAML）。
- 若 PyYAML 未安装，会**优雅降级**：尝试按 JSON 解析；失败则回退到内置
  默认 `security_audit` 配置，不因缺依赖而报错。
- 配置文件不存在或解析失败时同样回退到内置默认配置，并打印提示。

## 执行流程

`Scheduler.run(task_spec, task_input)` 的执行流程：

1. 按 `workflow` 顺序遍历步骤（如 `analyze → verify → report`）；
2. 每个步骤调用其对应 Agent（通过 skills registry / tool executor，当前为占位）；
3. 汇总每个步骤的结果，生成最终报告 dict。

返回的报告结构：

```python
{
    "task_type": "security_audit",
    "agents": ["repoaudit", "deepaudit", "argus"],
    "workflow": ["analyze", "verify", "report"],
    "steps": [   # 每个步骤的结果
        {"step": "analyze", "agent": "repoaudit", "output": {...}},
        {"step": "verify",  "agent": "deepaudit", "output": {...}},
        {"step": "report",  "agent": "argus",     "output": {...}},
    ],
    "summary": "任务类型「security_audit」执行完成：...",
}
```

## 使用方式

```python
from scheduler.config import load_config
from scheduler.executor import Scheduler

spec = load_config("configs/security_audit.yaml")
report = Scheduler().run(spec, {"target": "./project"})
print(report["summary"])
```

### 命令行验证

```bash
# 方式一
python -m scheduler

# 方式二
python -m scheduler.executor
```

两者都会：加载 `configs/security_audit.yaml` → 执行 analyze/verify/report →
打印各步骤结果与最终报告。
