# HOS-ARES APK 集成开发 Spec

## Why
将现有 AI Security Agent / Coding Agent / Terminal Agent 集成进 Android APK，统一入口复用已有的
reasonix-proot-app（Android Linux Runtime）与 Reasonix Agent Runtime，而非重新开发 Agent 框架。
目标是把开发量从"做一个 AI 安全平台"降低到"做一个 Android Agent 容器和安全插件生态"。

## What Changes
- 建立 HOS-ARES 项目骨架，明确分层架构：APK → reasonix-proot-app Runtime → Alpine Linux → AI Agent Gateway → Agent 层 → Security Tool Layer。
- 集成 reasonix-proot-app 作为 Android 宿主 Runtime，提供 Alpine Linux 运行环境。
- 以 Reasonix 作为统一 AI Agent 入口（第一选择 Agent OS），负责任务识别、Agent 调度与工具调用。
- 通过 Skill/Tool Registry 将安全 Agent（RepoAudit、DeepAudit、Argus、Strix、PentestGPT 等）作为技能插件接入，不写死工具。
- 实现任务示例：用户输入"审计这个项目" → 自动调度 RepoAudit → DeepAudit → Argus → 生成报告。
- 设计 Agent 调度 schema（task type / agents / workflow），定义 security-audit.skill 插件格式。
- 规划手机端-电脑端协同模式（WiFi/VPN 连接电脑 Agent Server 与 GPU 模型），手机作为控制端/报告终端。

## Impact
- 受影响的 spec：无（新项目启动）。
- 受影响的代码/系统：
  - `c:\1AAA-PROJECT\HOS\HOS-ARES`（项目根目录，当前仅含 PLAN.MD）
  - Android APK 层、reasonix-proot-app Runtime 集成、Reasonix Agent 配置、Skill/Tool Registry、Security Tool Layer。

## ADDED Requirements

### Requirement: 项目骨架与分层架构
系统 SHALL 建立 HOS-ARES 项目骨架，按 PLAN.MD 定义的分层架构组织代码，
包括 APK 层、Runtime 层（reasonix-proot-app）、Alpine 环境、AI Agent Gateway、Agent 层与 Security Tool Layer。

#### Scenario: 成功初始化骨架
- **WHEN** 开发者初始化 HOS-ARES 项目
- **THEN** 生成符合分层架构的目录结构与基础配置，各层职责清晰可扩展

### Requirement: 宿主 Runtime 集成（reasonix-proot-app）
系统 SHALL 集成 reasonix-proot-app 作为 Android Linux Runtime + Agent 宿主环境，
提供 Alpine Linux 环境以运行 Linux Agent。

#### Scenario: 成功启动 Linux 环境
- **WHEN** 在 APK 中启动宿主 Runtime
- **THEN** 进入 Alpine Linux 环境并可运行 Agent 进程

### Requirement: 统一 Agent 入口（Reasonix）
系统 SHALL 以 Reasonix 作为统一 Agent 入口，负责任务识别、Agent 调度与工具调用，
将安全场景下的任务分发到对应技能插件。

#### Scenario: 任务识别与调度
- **WHEN** 用户输入任务如"审计这个项目"
- **THEN** Reasonix 识别为代码审计任务，按 workflow 依次调用 RepoAudit、DeepAudit、Argus 并汇总报告

### Requirement: Skill/Tool Registry 与安全插件
系统 SHALL 提供 Skill/Tool Registry，以插件方式接入安全 Agent（不写死工具），
支持如 `security-audit.skill` 的声明式插件格式（name/tools/trigger）。

#### Scenario: 新增安全插件
- **WHEN** 开发者新增 `security-audit.skill`
- **THEN** 注册表识别该技能并可通过 trigger 触发对应审计工具链

### Requirement: Agent 调度配置
系统 SHALL 定义 Agent 调度 schema，描述 task type、参与 agents 与 workflow 顺序。

#### Scenario: 定义审计工作流
- **WHEN** 配置 security_audit 任务
- **THEN** 生成包含 repoaudit/deepaudit/argus 与 analyze/verify/report 流程的调度配置

### Requirement: 手机-电脑协同模式
系统 SHALL 支持手机端-电脑端协同：手机 HOS-ARES 作为控制端，通过 WiFi/VPN 连接电脑 Agent Server 与 GPU 模型，作为报告终端。

#### Scenario: 协同审计
- **WHEN** 手机无法本地承载重模型
- **THEN** 通过连接电脑 Agent Server 使用 GPU 模型执行审计并在手机端展示报告

## MODIFIED Requirements
（无，新项目启动）

## REMOVED Requirements
（无）
