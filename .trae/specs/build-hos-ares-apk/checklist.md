# Checklist

- [x] 项目骨架按分层架构（APK / Runtime / Alpine / Gateway / Agent / Security Tool Layer）创建
- [x] reasonix-proot-app 宿主 Runtime 集成并可启动 Alpine Linux 环境
- [x] Reasonix 统一 Agent 入口可识别任务并调度安全 Agent
- [x] Skill/Tool Registry 支持以插件方式接入安全 Agent，不写死工具
- [x] `security-audit.skill` 示例插件可用（name/tools/trigger）
- [x] Agent 调度配置（task type / agents / workflow）可声明式定义
- [x] security_audit 工作流（analyze/verify/report）可执行
- [x] 手机-电脑协同模式支持连接电脑 Agent Server 并在手机端展示报告
- [x] 示例任务"审计这个项目"端到端可运行并生成报告
