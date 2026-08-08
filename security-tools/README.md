# security-tools/ — Security Tool Layer（工具层）

## 职责
`security-tools/` 是 Security Tool Layer，负责统一封装并调度底层安全 Agent，
作为 **Agent / 技能层** 与 **agents/ 目录下真实 Agent 源码** 之间的薄封装层。

- 供上层（如技能层 `skills/`）调用的原子能力：
  - `repoaudit` — 仓库代码审计（scan）
  - `deepaudit` — 深度漏洞审计（analyze）
  - `argus`     — 安全扫描/监测（review）
  - `strix`     — AI 渗透测试（scan）
  - `pentestgpt`— AI 渗透测试（audit）
- 提供统一的执行入口 `ToolExecutor.exec(tool_name, target) -> ToolResult`。

## 结构
```
security-tools/
├── README.md                # 本文件：工具层职责说明
├── tool_executor.py         # ToolExecutor + ToolResult（统一调度入口）
└── adapters/                # 各安全 Agent 的真实源码适配器
    ├── __init__.py          # 适配器注册中心（run_agent / list_agents / get_adapter）
    └── base.py              # AgentAdapter 基类（构造命令 + PYTHONPATH + subprocess 执行）
```

## 与上层/底层的关系
```
技能插件（.skill/manifest.yaml 声明 tools: [repoaudit, deepaudit, argus]）
        │
        ▼
ToolExecutor.exec("repoaudit", "./project")
        │
        ▼
adapters.run_agent("repoaudit", "./project")   # 注册中心按名称取适配器
        │
        ▼
AgentAdapter.run() → build_cmd() 构造命令 + PYTHONPATH
        │
        ▼
subprocess 调用 agents/<name> 真实源码（如 agents/repoaudit/src/repoaudit.py）
```

> 说明：`adapters/` 直接定位 `agents/<name>` 目录下的真实源码，不再依赖全局 PATH。
> 若某 Agent 源码未拉取，`run()` 返回 `status="not_available"` 的占位结果。

## ToolResult 数据结构
`ToolExecutor.exec()` 返回 `ToolResult` 数据类，包含：
- `tool`: 工具名（repoaudit / deepaudit / argus / strix / pentestgpt）
- `target`: 目标路径/仓库
- `status`: `ok` / `error` / `not_available`
- `output`: 命令原始输出（stdout + stderr）
- `returncode`: 子进程返回码（未执行时为 None）
- `findings`: 解析出的结构化发现项列表

## 各 Agent 适配器入口
| 工具        | 适配器类                | 入口/命令                                       |
|-------------|-------------------------|-------------------------------------------------|
| `repoaudit` | `RepoAuditAdapter`      | `agents/repoaudit/src/repoaudit.py --project-path` |
| `argus`     | `ArgusAdapter`          | `python -m argus.cli scan <type> <target>`      |
| `strix`     | `StrixAdapter`          | `python -m strix -n -t <target> --scan-mode`    |
| `pentestgpt`| `PentestGPTAdapter`     | `python -m pentestgpt_agent.trial --goal ...`   |
| `deepaudit` | `DeepAuditAdapter`      | 服务式，需先启动 backend，经 REST API 触发（适配器暂为 API 集成占位） |

## 运行验证
```bash
python security-tools/tool_executor.py          # 验证 ToolExecutor.exec() 与注册中心
python security-tools/adapters/__init__.py      # 列出已注册 Agent
```
若某 Agent 源码未就位或缺 LLM 配置，`exec()` 返回对应 `status`（`not_available` / `error`）
并给出提示，脚手架仍可运行。
