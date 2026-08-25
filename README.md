# HOS-ARES

Android 端 AI 安全审计 Agent —— 以 **DeepSeek-Reasonix** 为统一 Agent 框架（cache-first），通过 MCP 安全工具链（mobile-security-mcp / mcp-termux / jadx-headless-mcp …）执行移动应用安全审计（SAST / SCA / 渗透测试）。

## 状态

| Phase | 内容 | 状态 |
|-------|------|------|
| 1 | 基础环境（reasonix CLI 验证） | ✅ 完成（v1.19.1，E2E 验收通过 ¥0.022/次） |
| 2 | MCP 安全工具链集成 | ✅ 完成（mobile-security-mcp 54 工具握手通过；demo MCP E2E 工具调用 ¥0.0013/次；**工具调用级兼容已闭环**：mcp-SDK 三世代双参派发 vs 包单参 handler 签名错位 → `tools/mcp-compat-gw.py` 适配层直调 handler，裸协议 4 帧 + reasonix 会话内 check_tools 派发 E2E 均通过，见 12 文） |
| 3 | AresGateway 网关（Kotlin） | 🔄 骨架完成（8 文件），serve 主通道协议已实测（Cookie 鉴权 + /events SSE）；传输实现在 Phase 4 |
| 4 | Android UI（Jetpack Compose） | 🔄 骨架完成（MainActivity/AresViewModel/AresHomeScreen + Manifest/网络安全配置 + Gradle 引入 ares-gateway）；serve E2E 链路实测基线已就绪（10-Phase4-端到端链路接通.md） |
| 5 | 打包与端到端测试 | 🔄 方案定稿：自包含装载（APK 内嵌 rootfs.tar.xz + proot 静态二进制，无需系统 Termux）；`scripts/build-rootfs.sh` 烘烤脚本 + `.github/workflows/build-apk.yml` 三 Job 流水线 + `RootfsInstaller`/`ReasonixServeBootstrap` 首启装载实现已落盘 |

## 目录结构

```
HOS-ARES/
├── 方案/HOS-ARES-重构方案-v3.0/   # 方案文档（含 07-实测偏差与修正）
├── config/reasonix.toml           # reasonix 项目配置模板（providers + MCP plugins）
├── scripts/
│   ├── setup-termux.sh            # Android Termux 部署（reasonix + MCP 工具链）
│   └── setup-proot-alpine.sh      # L5 容器（Termux + proot-distro Alpine）
├── ares-gateway/                  # L2 网关层（Kotlin，Phase 3）
├── android/                       # L1 Android App（Jetpack Compose，Phase 4）
├── container/                     # L5 容器运行时素材
├── tools/                         # 本地工具/依赖
└── docs/                          # 技术文档
```

## 快速开始（开发机）

```bash
# 一次性交互/多步 Agent 任务
reasonix run "对 sample.apk 做静态分析" --model deepseek-v4-flash

# 简单问答（流式 JSON 事件，适合 UI 展示）
reasonix -p "hello" --output-format stream-json

# AI 代码审查
reasonix review

# 服务模式（HTTP+SSE，AresGateway 首选通道）
reasonix serve --addr 127.0.0.1:8931 --auth token --token <TOKEN>

# MCP 服务器管理
reasonix mcp list          # 查看已注册（含工具/提示词/资源）
reasonix mcp add mobile-security -- python -m mobile_security_mcp
```

## Android 部署

```bash
# 在 Termux 内：
bash scripts/setup-termux.sh        # reasonix + MCP 工具链
bash scripts/setup-proot-alpine.sh  # （可选）L5 proot 容器
```

详见 `方案/HOS-ARES-重构方案-v3.0/` 与 `07-实测偏差与修正.md`。