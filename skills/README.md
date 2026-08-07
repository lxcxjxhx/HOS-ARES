# skills/ — Skill/Tool Registry（技能/工具注册中心）与技能插件目录

## 职责
`skills/` 目录承载两层内容：
1. **Skill/Tool Registry（注册中心）**：由 `registry.py` 实现，负责扫描、解析、
   索引声明式技能插件，并提供注册与触发查询能力。
2. **技能插件**：每个插件是一个 `.skill` 目录，内含 `manifest.yaml` 声明式元数据，
   例如 `security-audit.skill/`（安全审计技能示例）。

## 设计思路
HOS-ARES 通过 Skill/Tool Registry 以插件方式接入安全 Agent
（RepoAudit、DeepAudit、Argus、Strix、PentestGPT 等），**不在代码中写死工具**。
某个任务类型需要哪些工具，由对应技能插件的 `manifest` 声明；上层调度链
（如 `gateway/task_recognizer.py`）识别任务类型后，通过 trigger 匹配到技能，
再据此调用底层 Security Tool Layer（见 `security-tools/`）。

```
用户任务
   │
   ▼
gateway 任务识别（TaskRecognizer → task_type）
   │
   ▼
Skill/Tool Registry（registry.py）
   │  find_by_trigger(task_type) / get(name)
   ▼
技能插件（.skill/manifest.yaml: name / tools / trigger）
   │
   ▼
Security Tool Layer（security-tools/：repoaudit / deepaudit / argus）
```

## 声明式插件格式
每个技能插件目录下的 `manifest.yaml`（或 `SKILL.yaml`）定义元数据：

```yaml
name: security_audit      # 技能唯一名称
tools:                    # 依赖的底层工具列表
  - repoaudit
  - deepaudit
  - argus
trigger:                  # 可触发的任务类型列表
  - code_review
  - vulnerability
workflow: security_audit_flow   # 可选：对应编排流程名
```

字段说明：
- `name`: 技能唯一名称，用于 `SkillRegistry.get(name)`。
- `tools`: 该技能调用的底层工具命令（Security Tool Layer 中的原子能力）。
- `trigger`: 任务类型列表，用于 `SkillRegistry.find_by_trigger(task_type)` 匹配。
- `workflow`: 可选，该技能对应的编排流程名（供后续 workflow 引擎消费）。

## 如何注册与触发
```python
from skills.registry import SkillRegistry

reg = SkillRegistry("skills")          # 构造时自动 discover 扫描全部 .skill 插件

# 注册方式（二选一）
reg.discover("skills")                 # 1) 扫描目录批量注册
reg.register("skills/security-audit.skill")  # 2) 手动注册单个技能目录

# 触发/查询方式
skill = reg.get("security_audit")              # 按名称获取
skill = reg.find_by_trigger("vulnerability")   # 按任务类型（trigger）匹配
skills = reg.list()                            # 列出所有已注册技能
```

## 目录结构
```
skills/
├── README.md                  # 本文件：Registry 职责与插件格式说明
├── registry.py                # SkillRegistry 实现（含可运行验证）
└── security-audit.skill/      # 示例技能插件
    ├── manifest.yaml          # 声明式元数据（name/tools/trigger/workflow）
    └── README.md              # 技能用途与工具链说明
```

## 运行验证
```bash
python skills/registry.py
```
输出将显示已注册技能（security_audit）、按名称获取与按 trigger 匹配的结果。
