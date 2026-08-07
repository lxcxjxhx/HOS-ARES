# Tasks

- [x] Task 1: 初始化项目骨架：按 PLAN.MD 分层架构建立 HOS-ARES 目录结构与基础配置（APK / Runtime / Alpine / Gateway / Agent / Security Tool Layer）。
  - [x] SubTask 1.1: 设计并创建顶层目录结构（app、runtime、gateway、agents、security-tools、skills、docs）
  - [x] SubTask 1.2: 编写 README 说明分层架构与快速导航
  - [x] SubTask 1.3: 建立基础配置（构建脚本、环境变量模板）

- [x] Task 2: 集成 reasonix-proot-app 宿主 Runtime：接入 Android Linux Runtime + Alpine Linux 环境，作为 Agent 宿主。
  - [x] SubTask 2.1: 调研并锁定 reasonix-proot-app 集成方式与版本
  - [x] SubTask 2.2: 在 APK 中接入 Runtime 启动流程并验证 Alpine 环境可运行

- [x] Task 3: 接入 Reasonix 统一 Agent 入口：配置 Agent Runtime，实现任务识别、Agent 调度与工具调用。
  - [x] SubTask 3.1: 集成 Reasonix Agent 并配置默认模型/环境
  - [x] SubTask 3.2: 实现"审计这个项目"示例任务的识别与调度

- [x] Task 4: 建立 Skill/Tool Registry 与安全插件：以插件方式接入 RepoAudit、DeepAudit、Argus 等安全 Agent。
  - [x] SubTask 4.1: 定义 registry 数据结构与注册机制
  - [x] SubTask 4.2: 创建 `security-audit.skill` 示例插件（name/tools/trigger）
  - [x] SubTask 4.3: 实现安全工具层调用封装（repoaudit/deepaudit/argus）

- [x] Task 5: 实现 Agent 调度配置：定义 task type、agents、workflow 的声明式 schema。
  - [x] SubTask 5.1: 定义调度配置 schema（YAML/JSON）
  - [x] SubTask 5.2: 实现 security_audit 工作流（analyze/verify/report）

- [x] Task 6: 实现手机-电脑协同模式：支持 WiFi/VPN 连接电脑 Agent Server 与 GPU 模型，手机作为控制端/报告终端。
  - [x] SubTask 6.1: 设计协同通信协议与连接方式
  - [x] SubTask 6.2: 实现手机端调用远程 Agent Server 并在本地展示报告

# Task Dependencies
- [Task 2] depends on [Task 1]
- [Task 3] depends on [Task 1], [Task 2]
- [Task 4] depends on [Task 1], [Task 3]
- [Task 5] depends on [Task 3], [Task 4]
- [Task 6] depends on [Task 1], [Task 3]
