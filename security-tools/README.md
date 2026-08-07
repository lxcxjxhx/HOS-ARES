# security-tools/ — Security Tool Layer（工具层）

## 职责
`security-tools/` 是 Security Tool Layer，负责对底层安全工具命令进行统一封装，
作为 **Agent / 技能层** 与 **具体安全工具 CLI** 之间的薄封装层。

- 供上层（如技能层 `skills/`）调用的原子能力：
  - `repoaudit` — 仓库代码审计（scan）
  - `deepaudit` — 深度漏洞审计（analyze）
  - `argus`     — 安全扫描/监测（review）
- 提供统一的执行入口 `ToolExecutor.exec(tool_name, target) -> ToolResult`。

## 结构
```
security-tools/
├── README.md            # 本文件：工具层职责说明
├── tool_executor.py     # ToolExecutor + ToolResult（subprocess 占位实现，可运行）
└── tools/               # 各工具的独立封装模块（占位）
    ├── repoaudit.py     # RepoAuditTool.scan()
    ├── deepaudit.py     # DeepAuditTool.analyze()
    └── argus.py         # ArgusTool.review()
```

## 与上层的关系
```
技能插件（.skill/manifest.yaml 声明 tools: [repoaudit, deepaudit, argus]）
        │
        ▼
ToolExecutor.exec("repoaudit", "./project")
        │
        ▼
tools/repoaudit.py（或直接 subprocess 调用 CLI）
        │
        ▼
真实安全工具命令：repoaudit scan ./project
```

## ToolResult 数据结构
`ToolExecutor.exec()` 返回 `ToolResult` 数据类，包含：
- `tool`: 工具名（repoaudit / deepaudit / argus）
- `target`: 目标路径/仓库
- `status`: `ok` / `error` / `not_found`
- `output`: 命令原始输出（stdout + stderr）
- `returncode`: 子进程返回码（未执行时为 None）
- `findings`: 解析出的结构化发现项列表

## 真实环境命令示例
| 工具        | 命令                    | 方法       |
|-------------|-------------------------|------------|
| `repoaudit` | `repoaudit scan ./project`   | `scan()`   |
| `deepaudit` | `deepaudit analyze ./project` | `analyze()` |
| `argus`     | `argus review ./project`     | `review()` |

## 运行验证
```bash
python security-tools/tool_executor.py          # 验证 ToolExecutor.exec()
python security-tools/tools/repoaudit.py        # 验证各工具封装（deepaudit / argus 同理）
```
若对应命令未安装，`exec()` 返回 `status="not_found"` 的占位结果，脚手架仍可运行。
