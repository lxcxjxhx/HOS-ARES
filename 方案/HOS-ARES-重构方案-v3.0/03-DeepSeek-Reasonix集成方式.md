# 03 · DeepSeek-Reasonix 集成方式

## 3.1 为什么“不用反复重写 Agent 框架”

DeepSeek-Reasonix 本身就是**完整的 Agent 框架**，而非需要二次开发的引擎。它已提供：

1. **Agent 循环**：cache-first loop，专为 DeepSeek 前缀缓存优化
2. **工具调用**：外部工具以 stdio JSON-RPC 子进程运行（MCP 兼容）
3. **插件系统**：MCP Server 贡献工具、提示词、资源
4. **子 Agent**：Planner 与子 Agent 支持可信 MCP
5. **权限管理**：deny > ask > allow > fallback
6. **配置驱动**：provider、agent、启用工具全部可配置
7. **自定义斜杠命令**：v1.25.0 支持自定义斜杠命令

**HOS-ARES 需要做的**：不是“重写 Agent 框架”，而是：

- 将 DeepSeek-Reasonix 打包进 APK（通过 Termux 环境）
- 配置 `~/.reasonix/config.json` 注册 MCP Server
- 通过 Android 端调用 DeepSeek-Reasonix CLI

## 3.2 DeepSeek-Reasonix MCP 配置示例

在 Termux 容器中，通过 `~/.reasonix/config.json` 配置 MCP Server：

```json
{
  "mcpServers": {
    "mobile-security": {
      "command": "python",
      "args": ["-m", "mobile_security_mcp"],
      "env": {
        "DEEPSEEK_API_KEY": "${DEEPSEEK_API_KEY}"
      }
    },
    "mcp-termux": {
      "command": "mcp-termux",
      "args": ["--port", "65534"]
    },
    "jadx-headless": {
      "command": "jadx-headless-mcp",
      "args": []
    }
  }
}
```

安装 MCP Server 本身就是授权决策——DeepSeek-Reasonix 会：

1. 自动发现所有已注册 MCP Server 暴露的工具
2. 通过 `/mcp` 命令列出已连接的服务器及其暴露的内容
3. 在 Agent 循环中按需调用 MCP 工具

## 3.3 HOS-ARES 调用 DeepSeek-Reasonix 的方式

Android 端通过 AresGateway 调用 DeepSeek-Reasonix CLI：

```kotlin
// AresGateway.kt
suspend fun executeTask(task: String, onLine: (String) -> Unit): Result<String> {
    // 在 Termux 容器中执行 DeepSeek-Reasonix
    val command = listOf(
        "proot", "-0", "-r", "/data/data/com.termux/files/usr/var/lib/proot-distro/installed-rootfs/alpine",
        "/bin/sh", "-c",
        "reasonix ask \"$task\" --model deepseek-v4-flash --output stream"
    )
    return runCommand(command, onLine)
}
```

DeepSeek-Reasonix 会：

1. 自动利用前缀缓存（会话保持不重置）
2. 通过 MCP 调用安全工具
3. 流式输出结果（通过 stdout）

## 3.4 成本优势量化

DeepSeek-Reasonix 的缓存优化效果已有真实案例验证：单日 4.35 亿输入 token，缓存命中率 **99.82%**，费用仅约 **$12**，而同等工作量在 v4-flash 上无缓存时需约 **$61**。这意味着 HOS-ARES 在重度使用场景下 API 成本可降低约 **80%**。

---

*上一份：`02-整体架构设计.md` · 下一份：`04-MCP安全工具链.md`*