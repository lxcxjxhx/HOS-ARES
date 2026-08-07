# gateway/ — AI Agent Gateway（统一 Agent 入口）

## 一、职责

AI Agent Gateway 层是 HOS-ARES 面向用户的**统一 Agent 入口**，负责：

- 接收用户的自然语言任务（如"审计这个项目"）
- 调用 **任务识别器（TaskRecognizer）** 判断任务类型
- 通过 **Reasonix**（第一选择 Agent OS）进行 Agent 调度与工具调用
- 汇聚各安全 Agent 技能插件的执行结果与审计报告
- 在 Android 内以 HTTP/本地服务形式暴露统一网关

## 二、与 Reasonix 的关系

HOS-ARES **不重新开发 Agent 框架**，而是**复用 Reasonix Agent Runtime 作为统一入口**。

```
用户输入任务
   │
   ▼
┌─────────────────────────────┐
│  AI Agent Gateway           │  统一入口 / 编排层
│  ├─ TaskRecognizer          │  任务识别（关键词/规则 → LLM）
│  └─ ReasonixAgent           │  调用 Reasonix Agent Runtime
└─────────────────────────────┘
   │  调度 & 工具调用
   ▼
┌─────────────────────────────┐
│  Reasonix Agent Runtime     │  第一选择 Agent OS（复用，不重开发）
└─────────────────────────────┘
   │  按任务类型调度安全 Agent 技能插件
   ▼
┌──────────────────────────────────────────────┐
│  安全 Agent 技能插件（Reasonix 调度）          │
│  RepoAudit → DeepAudit → Argus → 生成报告     │
└──────────────────────────────────────────────┘
```

本目录是**脚手架**：`agent_gateway.py` / `task_recognizer.py` 提供可验证的结构与占位实现，
真实环境中 `ReasonixAgent.run()` 会调用 Reasonix Agent Runtime（CLI/API）。

## 三、核心流程：任务识别 → 调度 → 工具调用 → 报告

以任务"审计这个项目"为例：

1. **提交任务**：用户将自然语言任务交给 `AgentGateway.submit(task)`。
2. **任务识别**：`TaskRecognizer.recognize(task)` 解析任务类型。
   - 脚手架阶段：基于关键词/规则（如包含"审计 / audit / vulnerability / 漏洞" → `security_audit`）。
   - 后续：预留 LLM 识别接口，由大模型做更灵活的意图分类。
3. **调度**：`AgentGateway` 根据任务类型选择调度链（workflow），交由 `ReasonixAgent` 执行。
4. **工具调用**：Reasonix Agent Runtime 调度安全 Agent 技能插件链：
   `RepoAudit（仓库审计） → DeepAudit（深度审计） → Argus（漏洞验证/风险评估）`
5. **生成报告**：将各 Agent 输出汇聚为统一报告，封装进 `TaskResult` 返回。

## 四、目录结构

```
gateway/
├── README.md             # 本文件
├── agent_gateway.py      # AgentGateway 统一入口类
└── task_recognizer.py    # TaskRecognizer 任务识别器
```

## 五、后续填充（真实实现）

- 对接 Reasonix Agent Runtime 的真实 CLI/API 调用
- 接入 Android 内 HTTP/本地服务暴露的统一网关
- 将 `TaskRecognizer` 升级为 LLM 驱动的意图识别
- 打通各安全 Agent 技能插件（RepoAudit / DeepAudit / Argus）的返回结果

> 说明：本目录为纯脚手架，当前为占位实现，核心目标是**结构清晰、职责明确**，便于后续填充真实逻辑。
