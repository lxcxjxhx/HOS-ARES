# HOS ARES

> Android 上的 AI Security / Coding / Terminal Agent 一体化工作台
> 在手机端通过 proot 在 Alpine Linux 环境中运行多个安全/编码 Agent，统一由 HOS-ARES 网关调度。

## 是什么

HOS-ARES 是一款 Android 原生应用（Kotlin + Jetpack/ViewBinding），内置一个 proot-based Alpine Linux 运行时，把多个真实的安全/编码 Agent（RepoAudit、DeepAudit、Argus、Strix、PentestGPT）打包进 APK 资产，在手机上本地执行。UI 采用 Codex/Claude 风格的结构化任务交互：逐 Agent 状态卡片 + 详情弹窗 + 流式输出。

- **品牌**：HOS 为品牌名，ARES 为产品 / Agent 平台名（应用名 "HOS ARES"）。
- **默认 LLM 后端**：DeepSeek（`deepseek-chat`），也可配置 Claude / OpenAI / Gemini / 本地。
- **默认工作目录**：未配置时自动使用 `/sdcard/.ares/project/<命名或随机数>`。

## 分层架构

```
APK 层 (app/)                  Android 应用入口 · 任务卡侧边栏 · Agent 状态卡片 · 详情弹窗
Android Linux Runtime (proot)  在无 root 的 Android 上以用户态 chroot 运行 Alpine Linux
Alpine Linux                   由 app/ares-rootfs 提供的 minirootfs（含 python3 + pip）
AI Agent Gateway (gateway/)    Agent 统一入口与调度（任务识别 → 逐 Agent 调度）
Agent 层 (agents/)             repoaudit · deepaudit · argus · strix · pentestgpt 等
Security Tool Layer            repoaudit / deepaudit / argus 等命令封装
Skills Registry (skills/)      技能注册与 token 优化 skill（ponytail / CodeGraph / ast-grep 等）
```

## 仓库导航

| 目录 | 职责 |
| --- | --- |
| [`app/`](app/) | Android APK 源码（`app/app/src/main/` 为应用代码与资源；`app/ares-rootfs/` 为运行时资产） |
| [`runtime/`](runtime/) | Alpine Linux 运行环境与宿主集成 |
| [`gateway/`](gateway/) | AI Agent Gateway：统一入口与任务调度 |
| [`agents/`](agents/) | 各 Agent 接入：repoaudit / deepaudit / argus / strix / pentestgpt / reasonix 等 |
| [`security-tools/`](security-tools/) | 安全工具层命令封装 |
| [`skills/`](skills/) | Skill/工具注册中心与技能插件 |
| [`configs/`](configs/) | 安全审计等配置 |
| [`coop/`](coop/) | 协同协议（server/client/security） |

## 构建 APK

环境要求：JDK 17、Android SDK (platform-34 / build-tools 34.0.0)、Gradle 8.7。

```powershell
# 在项目根目录执行
.\build.ps1
```

产物输出到 `build/HOS-ARES-debug.apk`（约 13 MB，含 proot、Alpine minirootfs、Agent 运行时与启动脚本等资产）。

> `local.properties`（本机 SDK 路径）已被 `.gitignore` 忽略，克隆后请自行配置或由构建脚本设置。

## 使用

1. 安装 APK 后首次启动会自动在后台完成初始化（解包 Alpine rootfs、安装 proot、部署 Agent 脚本）。
2. 侧边栏可管理任务卡（每个工作目录一张卡，目录去重）。
3. 在「设置」中配置 LLM Key（默认 DeepSeek，也可一键填入默认配置）。
4. 输入工作目录（留空使用默认 `/sdcard/.ares/project`）与任务描述，点击「执行」。

## 环境变量

复制 `.env.example` 为 `.env` 并填写（`.env` 已被忽略，勿提交）。Android 端 LLM Key 通过应用内设置界面持久化并注入 rootfs 环境变量（`DEEPSEEK_API_KEY2` / `HOS_BACKEND` / `HOS_MODEL` 等）。

## 说明

本项目为个人原创实现，参考了 reasonix-proot-app 的 proot 方案思路，但 Android 宿主源码、网关、UI 与运行脚本均为本项目自研。
