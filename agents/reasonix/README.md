# reasonix — Reasonix Agent（统一 Agent OS 入口）

## 一、职责

- **Reasonix Agent 接入配置与运行封装**
- 本项目**默认 / 核心 Agent**，作为统一 Agent 入口（第一选择 Agent OS），
  由 reasonix-proot-app 与 Reasonix Agent Runtime 提供支持
- HOS-ARES **复用** Reasonix Agent Runtime，**不重新开发** Agent 框架

## 二、接入方式

Reasonix 作为统一 Agent 入口，负责任务理解、Agent 调度与工具调用。

```
gateway/AgentGateway.submit(task)
        │  调度
        ▼
agents/reasonix/agent.py  ReasonixAgent.run(task, config)
        │  调用 Reasonix Agent Runtime（CLI/API）
        ▼
Reasonix Agent Runtime  识别任务 → 调度安全 Agent 技能插件
        │  RepoAudit → DeepAudit → Argus → 生成报告
        ▼
返回结果汇聚为 TaskResult / 报告
```

接入步骤（真实实现）：
1. 通过 `ReasonixAgent` 封装类提交任务（`agent.py`）；
2. 读取默认配置（`config.yaml` 或环境变量 `HOS_ARES_REASONIX_MODEL`、`HOS_ARES_API_KEY`）；
3. 调用 reasonix CLI / API 完成调度与工具调用；
4. 汇聚各安全 Agent 技能插件（RepoAudit / DeepAudit / Argus）输出为报告。

## 三、配置

默认配置见 `config.yaml`；敏感配置（API Key 等）通过环境变量注入：

| 环境变量 | 说明 |
| --- | --- |
| `HOS_ARES_REASONIX_MODEL` | Reasonix 默认模型 |
| `HOS_ARES_API_KEY` | 大模型 API Key |
| `REASONIX_API_KEY` | Reasonix 服务 API Key |
| `REASONIX_PORT` | Reasonix 服务端口（Android 内本地服务，默认 8080） |

## 四、目录结构

```
agents/reasonix/
├── README.md       # 本文件
├── agent.py        # ReasonixAgent 封装类
└── config.yaml     # 默认配置
```

## 五、后续填充（真实实现）

- 对接 reasonix CLI / API 的真实调用
- 打通「审计这个项目」示例任务的完整调度链（调用 gateway 展示调用链）
- 接入 Android 内 reasonix-proot-app / Reasonix Agent Runtime

> 说明：本目录为纯脚手架，当前为占位实现，核心目标是**结构清晰、职责明确**。
