# agents/ — Agent 接入配置与封装

## 职责
- 各 AI Agent 的接入配置与统一封装
- 支持的 Agent：
  - `reasonix`     — Reasonix Agent
  - `claude-code`  — Claude Code
  - `opencode`     — OpenCode
  - `openhands`    — OpenHands
  - `pentestgpt`   — PentestGPT（渗透测试）
  - `strix`        — Strix（安全审计）
  - `repoaudit`    — RepoAudit（仓库审计）
  - `deepaudit`    — DeepAudit（深度审计）
  - `argus`        — Argus（安全 Agent）

## 子目录说明
每个 Agent 一个子目录，各自放置接入配置、运行脚本与封装占位文件。
