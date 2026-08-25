# AresGateway 网关层设计（L2 · Kotlin）

> 依据实测（reasonix v1.19.1）修正后的集成设计，与方案 v3.0 的偏差见 `方案/HOS-ARES-重构方案-v3.0/07-实测偏差与修正.md`。

## 1. 职责

- 接收 Android UI（L1）的任务输入，识别意图并路由到对应 Skill
- 通过 reasonix 执行任务：**首选 `reasonix serve`（HTTP+SSE）**，备选子进程 `reasonix run --events-jsonl` / `-p --output-format stream-json`
- 将结构化事件流映射为任务卡片状态机（PENDING → RUNNING → TOOL → DONE/FAILED）
- 聚合成本（usage 事件）与会话复用（前缀缓存友好），做预算门控

## 2. 传输通道（实测命令面）

| 通道 | 命令 | 适用场景 | 说明 |
|------|------|---------|------|
| **serve（首选）** | `reasonix serve --addr 127.0.0.1:8931 --auth token --token <T>` | 长会话/多任务/缓存复用 | HTTP+SSE，天然支持 Android 端长连接 |
| run | `reasonix run <task> --output-format stream-json`（或 `--events-jsonl`） | 多步安全审计任务 | 结构化事件 JSONL，任务卡片直驱 |
| print | `reasonix -p <task> --output-format stream-json` | 简单问答 | 流式 JSON 事件（实测：turn_started→retrying→reasoning→text→message→usage→result） |

子进程通道在 Termux 容器内经 proot 调用（Android 端）；serve 通道为本机 loopback。

## 3. 实测事件流 → 任务卡片状态映射

实测 `stream-json` 事件 kind（v1.19.1）：

| kind | 含义 | 卡片状态 |
|------|------|---------|
| `turn_started` | 回合开始 | RUNNING（进入） |
| `retrying` | 自动重试 | RUNNING（角标 RETRY） |
| `reasoning` | 推理文本 | RUNNING（思考区） |
| `tool_dispatch` / `tool_result` | MCP/工具调用与结果 | **TOOL**（展示工具名、参数、耗时、输出） |
| `text` / `message` | 输出文本 | RUNNING（流式正文） |
| `usage` | token/缓存/费用明细 | 成本累计（只读字段） |
| `result` | 最终结果 + session_id | DONE（成功）/ FAILED |

`run --events-jsonl` 输出脱敏结构化事件（含 task 元数据），用于审计留痕。

## 4. SkillRegistry

```kotlin
Skill(id, name, keywords, model, mcpHint, description)
```

| skillId | 名称 | 关键词（示例） | 说明 |
|---------|------|--------------|------|
| `apk-static` | APK 静态分析 | apk, 反编译, 静态分析, 逆向, dex | apktool/jadx → mobile-security-mcp |
| `dynamic-hook` | 动态插桩 | frida, 插桩, hook, 运行时 | mobile-security-mcp（spawn/attach） |
| `rasp-bypass` | RASP 绕过 | rasp, dexguard, 加固识别 | mobile-security-mcp |
| `sca-audit` | 依赖漏洞审计 | sca, 依赖, cve, 组件漏洞 | 容器内工具链 |
| `pentest` | 渗透测试 | 渗透, 漏洞利用, 越权 | mcp-termux（stackplz/radare2 等） |
| `chat` | 通用问答（默认） | （兜底） | -p 直接调用 |

匹配规则：关键词计分 → 最高分命中；无命中走 `chat` 兜底。

## 5. 任务生命周期

```
submit(input) → classify → create TaskCard(PENDING)
→ 连接 reasonix（serve 或子进程）
→ 事件流映射状态（RUNNING/TOOL/…）
→ usage 事件累计成本
→ result 事件 → DONE 或 FAILED
cancel(id) → 中断传输 → CANCELLED
```

- 会话复用：同一 Skill 的任务共享一个 reasonix 会话（serve 长连接），最大化前缀缓存命中（实测跨会话命中≈99.5%）。
- 预算门控：累计成本超阈值 → 提示确认或自动降级到 `deepseek-v4-flash`（输入 ¥1/M、输出 ¥2/M、缓存命中 ¥0.02/M）。

## 6. 代码结构（本目录）

```
ares-gateway/src/main/kotlin/com/hos/ares/gateway/
├── TaskCard.kt        # 任务卡片 + 状态机
├── ReasonixEvent.kt   # 事件模型与 JSONL 解析
├── Skill.kt           # Skill 描述 + 内置 Skill 清单
├── SkillRegistry.kt   # 关键词路由
├── ReasonixClient.kt  # 传输抽象（serve/子进程两类实现）
└── AresGateway.kt     # 网关门面：submit/cancel/流式回调/成本聚合
```

依赖：kotlinx-coroutines（Flow）、OkHttp/SSE（serve 通道）、org.json。Gradle 详见 `ares-gateway/build.gradle.kts`（Phase 5 打包时纳入 APK）。