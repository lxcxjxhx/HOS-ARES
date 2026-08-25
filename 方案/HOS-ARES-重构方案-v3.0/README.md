# HOS-ARES 重构方案 v3.0 · 文档索引

> 核心决策：以 **DeepSeek-Reasonix** 为统一 Agent 框架，从"重写 Agent 框架"转变为"集成配置"。
> 方案状态：✅ 已定稿 · 版本 v3.0 · 日期 2026-08

---

## 一、方案一句话

将 HOS-ARES 的 Agent 层从"自研 Python 软路由 + ThreadPoolExecutor"整体替换为 **DeepSeek-Reasonix**（开源即用完整 Agent 框架），通过 Termux + proot-distro 容器打包进 APK，MCP 安全工具链（mobile-security-mcp 等）直接注册复用，开发重点转向 **Android UI + AresGateway 网关 + MCP 配置**，API 成本预计降低约 **80%**。

---

## 二、文档结构（按阅读顺序）

| 序号 | 文件 | 内容 | 对应章节 |
|------|------|------|---------|
| 1 | `01-核心决策-统一Agent框架.md` | 为什么选 DeepSeek-Reasonix、Android/Termux 运行机制 | 一 |
| 2 | `02-整体架构设计.md` | 五层架构（L1 UI → L5 容器）、各层职责 | 二 |
| 3 | `03-DeepSeek-Reasonix集成方式.md` | 集成方式、MCP 配置示例、调用封装、成本量化 | 三 |
| 4 | `04-MCP安全工具链.md` | 可复用 MCP Server 清单与安装方式 | 四 |
| 5 | `05-实施路线图.md` | 五个 Phase 的时间与任务拆分 | 五 |
| 6 | `06-新旧方案对比与总结.md` | 原方案 vs 新方案关键差异、总结 | 六、七 |
| 7 | `07-实测偏差与修正.md` | Phase 1 实测验收、方案假设 vs 实测事实对照、修正后集成架构（reasonix v1.19.1 实测，2026-08-25） | 补充 |
| 8 | `08-Phase2-MCP工具链验收.md` | Phase 2 验收：MCP 工具链握手/发现/调用实测、mobile-security-mcp 54 工具清单、mcp SDK 版本兼容结论 | 补充 |
| 9 | `09-Phase3-AresGateway验收.md` | Phase 3 验收：serve 通道协议实测（Cookie 鉴权 + /events SSE）、AresGateway Kotlin 骨架 8 文件与编译修正 | 补充 |
| 10 | `10-Phase4-端到端链路接通.md` | Phase 4 前置验收：serve submit→SSE 全链路实测（202→事件帧→turn_done 收尾）、Cookie 制/端点/双收尾协议结论、Android 联调基线 | 补充 |
| 11 | `11-Phase5-APK全量打包方案.md` | Phase 5 打包：自包含装载（APK 内嵌 Alpine rootfs + proot 静态二进制，不依赖系统 Termux）、Tier 体积预算、CI 三 Job 流水线、偏差与风险记录 | 补充 |
| 12 | `12-Phase5-前置：工具调用级兼容探测.md` | **工具调用级兼容**：实测三世代 mcp-SDK 派发签名错位（单参 handler vs 双参派发）→ `tools/mcp-compat-gw.py` 适配层（绕 SDK 直调 handler）→ 裸协议 4 帧 + reasonix 会话内 E2E 双验证通过 | 补充 |

---

## 三、核心指标速览

| 指标 | 值 |
|------|-----|
| Agent 框架 | DeepSeek-Reasonix（32.9k GitHub Stars，2026-08） |
| 缓存命中率 | 99.82%（真实案例：单日 4.35 亿输入 token） |
| 成本对比 | 有缓存约 $12/日 vs 无缓存约 $61/日（降幅 ~80%） |
| 部署形态 | 单 Go 静态二进制 / npm i -g reasonix，WASM 兼容 Termux |
| MCP 集成 | 内置客户端，stdio + Streamable HTTP |
| 子 Agent | Planner + 子 Agent，支持可信 MCP |
| 权限管理 | deny > ask > allow > fallback |
| 实施周期 | 约 6-10 周（5 个 Phase） |

---

## 四、关键结论

1. **零 Agent 框架开发**：DeepSeek-Reasonix 已提供完整的 Agent 循环、MCP 客户端、插件系统、子 Agent、权限管理。
2. **DeepSeek 原生优化**：cache-first loop，缓存命中率 99.82%，API 成本降低约 80%。
3. **MCP 开箱即用**：内置 MCP 客户端，支持 stdio + Streamable HTTP，安全工具链直接注册复用。
4. **Android 完美兼容**：WASM 替代原生模块，Termux 上功能完全一致。
5. **生态成熟**：32.9k GitHub Stars，活跃社区。

---

*本文档目录：`方案/HOS-ARES-重构方案-v3.0/`*