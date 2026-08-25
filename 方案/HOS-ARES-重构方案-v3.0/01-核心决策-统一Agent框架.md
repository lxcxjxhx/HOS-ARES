# 01 · 核心决策：以 DeepSeek-Reasonix 为统一 Agent 框架

## 1.1 为什么选择 DeepSeek-Reasonix 作为 Agent 框架

**DeepSeek-Reasonix**（注意：全称是 **DeepSeek-Reasonix**，不是 Reasonix——Reasonix 是其 CLI/TUI 的命名，整个项目官方名称是 DeepSeek-Reasonix）是一个面向终端的 DeepSeek 原生 AI 编程智能体。截至 2026 年 8 月，GitHub 标星已达 **32.9k**，其核心设计完全围绕 DeepSeek 前缀缓存优化——会话保持不重置，缓存命中率越高，单价越低。

**选择 DeepSeek-Reasonix 而非 LangGraph/Pydantic AI 作为 Agent 框架的核心理由：**

| 维度 | DeepSeek-Reasonix | LangGraph + Pydantic AI | 决策 |
|------|-------------------|-------------------------|------|
| **DeepSeek 优化** | ✅ 原生设计，99.82% 缓存命中率 | ❌ 通用框架，无特殊优化 | **DeepSeek-Reasonix 胜出** |
| **MCP 集成** | ✅ 内置完整 MCP 客户端，支持 stdio + Streamable HTTP | ⚠️ 需通过 Pydantic AI MCPToolset 桥接 | **DeepSeek-Reasonix 胜出** |
| **部署复杂度** | ✅ 单 Go 静态二进制，npm i -g 即可 | ❌ 需 Python 环境 + 多依赖 | **DeepSeek-Reasonix 胜出** |
| **Android 兼容性** | ✅ WASM 替代原生模块，Termux 完美运行 | ⚠️ Python 在 Android 上可运行但较重 | **DeepSeek-Reasonix 胜出** |
| **插件驱动** | ✅ MCP Server 提供工具、提示词和资源 | ✅ 同样支持 | 持平 |
| **自定义斜杠命令** | ✅ v1.25.0 支持 | ❌ 需额外实现 | **DeepSeek-Reasonix 胜出** |
| **子 Agent 支持** | ✅ Planner 与子 Agent 支持可信 MCP | ✅ LangGraph 原生支持 | 持平 |

**结论**：DeepSeek-Reasonix 是一个**开箱即用的完整 Agent 框架**，而非需要二次开发的引擎。以它为核心，可以**零代码**获得：

- 完整的 Agent 循环（cache-first loop）
- 内置 MCP 客户端（stdio + Streamable HTTP）
- 插件驱动架构（MCP Server 贡献工具、提示词、资源）
- 权限门控（deny > ask > allow > fallback）
- 子 Agent 支持（Planner 与子 Agent 可信 MCP）
- WASM 兼容（Termux 上完美运行）

## 1.2 DeepSeek-Reasonix 在 Android/Termux 上的运行机制

DeepSeek-Reasonix 的 npm 构建会自动将 WASM 版的 tree-sitter-*.wasm 拷贝到 `dist/grammars/`，运行时用 WASM 代替原生模块，功能完全一致。在 Termux 中安装只需：

```bash
pkg install nodejs-lts
npm i -g reasonix  # 任意操作系统，拉取预构建的原生二进制文件
reasonix setup     # 30 秒配置向导
```

安装后，DeepSeek-Reasonix 通过 `~/.reasonix/config.json` 中的 `mcpServers` 配置自动连接 MCP Server。

---

*上一份：`README.md` · 下一份：`02-整体架构设计.md`*