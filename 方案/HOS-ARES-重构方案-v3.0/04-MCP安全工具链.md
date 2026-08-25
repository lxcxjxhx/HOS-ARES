# 04 · MCP 安全工具链（直接复用）

DeepSeek-Reasonix 内置 MCP 客户端，以下 MCP Server 可直接在 `~/.reasonix/config.json` 中注册使用。

## 4.1 mobile-security-mcp

将任何 AI Agent 转变为 Android 安全研究员的 MCP 服务器：

- **静态分析**：APK 反编译（apktool）、Java 反编译（jadx）、APK 识别（apkid）、密钥扫描
- **动态插桩**：Frida spawn/attach/inject、Objection
- **RASP 绕过**：Zimperium/DexGuard/Promon/Arxan 识别与绕过
- **跨平台**：Hermes 解码（React Native）、Flutter Blutter（Flutter）
- **设备控制**：ADB shell、scrcpy

安装方式：`pip install mobile-security-mcp`

## 4.2 mcp-termux v7.0

Android 逆向工程一体化 MCP 服务器，提供 **73 个工具**：

- stackplz eBPF 动态追踪
- paradise 内存分析
- radare2 静态分析
- ARM64 Root 支持

## 4.3 其他可复用 MCP Server

| MCP Server | 描述 | 安装方式 |
|-----------|------|---------|
| **Android-RE** | 5 个 MCP Server，封装 Apktool、jadx、androguard、LIEF、Frida、ADB | 源码构建 |
| **jadx-headless-mcp** | 单进程无头 jadx，支持 295MB/55 dex 的大应用反编译 | npm install |
| **droid-mcp** | Termux 原生 MCP 服务器，Rust 编写 | cargo build |
| **termux-mcp** | Termux 环境 MCP 协议实现 | npm install |

## 4.4 注册清单（建议启用）

```json
{
  "mcpServers": {
    "mobile-security":     { "command": "python", "args": ["-m", "mobile_security_mcp"] },
    "mcp-termux":          { "command": "mcp-termux", "args": ["--port", "65534"] },
    "jadx-headless":       { "command": "jadx-headless-mcp", "args": [] },
    "android-re":          { "command": "android-re", "args": [] },
    "droid-mcp":           { "command": "droid-mcp", "args": [] }
  }
}
```

---

*上一份：`03-DeepSeek-Reasonix集成方式.md` · 下一份：`05-实施路线图.md`*