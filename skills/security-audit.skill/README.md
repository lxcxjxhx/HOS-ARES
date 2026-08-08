# security-audit.skill — 安全审计技能插件（示例）

## 用途
`security_audit` 是一个声明式技能插件，用于对目标代码仓库执行安全审计。
它通过 Skill/Tool Registry 注册，由任务类型（trigger）触发，并调用底层的
Security Tool Layer（工具链）完成实际扫描。

## 工具链（tools）
该技能依赖以下底层安全工具（见 `security-tools/`），统一通过
`security-tools/adapters/__init__.py::run_agent(name, target)` 调度真实 Agent 源码：

| 工具        | 作用                       | 对应适配器                           |
|-------------|----------------------------|--------------------------------------|
| `repoaudit` | 仓库代码审计（scan）       | `security-tools/adapters` → `RepoAuditAdapter` |
| `deepaudit` | 深度漏洞审计（analyze）    | `security-tools/adapters` → `DeepAuditAdapter`（API 集成占位） |
| `argus`     | 安全扫描/监测（review）    | `security-tools/adapters` → `ArgusAdapter`     |

## 触发方式
本技能声明了如下任务类型（trigger）：
- `code_review` — 代码评审/代码审计类任务
- `vulnerability` — 漏洞排查/安全扫描类任务

当上层调度链（如 gateway 的 TaskRecognizer）识别出上述任务类型时，
可通过 `SkillRegistry.find_by_trigger(task_type)` 匹配到本技能。

## 使用方式（示例）
```
> 使用 security_audit 技能对 <repo> 进行代码审计
> 对 <repo> 执行漏洞扫描（vulnerability）
```

代码层面：
```python
from skills.registry import SkillRegistry

reg = SkillRegistry("skills")
skill = reg.get("security_audit")              # 按名称获取
skill = reg.find_by_trigger("vulnerability")   # 按任务类型获取
print(skill.tools)   # ['repoaudit', 'deepaudit', 'argus']
```

## manifest 说明
本目录的 `manifest.yaml` 即为声明式插件元数据，字段含义：
- `name`: 技能唯一名称（`security_audit`）
- `tools`: 调用的底层工具列表
- `trigger`: 可触发的任务类型列表
- `workflow`: 对应编排流程名（可选）
