# 10 · Phase 4 前置验收：serve 端到端链路接通（Android 客户端联调基线）

> 实测日期：2026-08-25 · reasonix serve v1.19.1 · 目标：AresGateway 主通道（HTTP+SSE）的**提交→事件流**完整闭环，作为 Phase 4 Android 客户端联调基线。

## 10.1 闭环实测记录 ✅

| 步骤 | 命令 | 结果 |
|------|------|------|
| 1. 鉴权握手 | `GET /?token=<T>` | 200 + `Set-Cookie: reasonix_token=<T>`（HttpOnly） |
| 2. SSE 建链 | `GET /events`（带 Cookie） | 200，首帧 `: connected`，随后周期 `: ping` 心跳 |
| 3. 任务提交 | `POST /submit`，body `{"input":"只回复：SSE-OK"}`（`Content-Type: application/json`，带 Cookie） | **202 Accepted**（Content-Length: 0） |
| 4. 事件回流 | 同一 /events 连接收到实际任务事件 | 见下方完整帧序列 |

**SSE 帧序列（tools/gw-events2.txt，1685 字节 / 58 行）：**

```
: connected
: ping
data: {"kind":"turn_started"}
data: {"kind":"reasoning","text":"用户"}          ← 逐字推理流（可渲染"思考区"）
data: {"kind":"text","text":"S"}                 ← 逐字输出流（正文逐字渲染）
data: {"kind":"message","text":"SSE-OK","reasoning":"..."}   ← 整句消息
data: {"kind":"usage","usage":{promptTokens:27599,...,cost:0.0194914,"currency":"¥",cacheDiagnostics:{...}}}  ← 成本+缓存统计
data: {"kind":"turn_done"}                       ← 回合收尾
```

## 10.2 关键协议结论（已固化进 AresGateway 骨架）

1. **鉴权 = Cookie 制**：`GET /?token=` 握得 `reasonix_token` Cookie（HttpOnly），后续 `/events` 与 `/submit` 全部凭 Cookie——**不是 Bearer**（07 文假设修正，09 文协议实测细化）。
2. **SSE 端点为 `/events`**：`/sse` 为旧路径（302 → /events）。连接建立后先收 `: connected` 与周期 `: ping` 心跳帧（客户端应忽略注释/无 data 帧）。
3. **提交端点 `/submit`**：body `{"input":"<任务文本>"}`，返回 **202**；同一 /events 连接的多次 /submit 构成**长会话**——前缀缓存跨轮复用（本帧首轮已命中 cacheHit=8320/27599≈30%，后续轮次应持续抬升，对应方案缓存成本主张）。
4. **serve 收尾事件 = `turn_done`**（无 `result` 帧，与 `-p` 模式不同）→ AresGateway 状态机已加 `TurnDone` 分支（DONE 补收尾）；成本在 `usage` 帧累计。
5. 其他已探测端点（页面 JS 确认）：`/approve` `/answer` `/cancel` `/new` `/compact` `/history` `/status` `/models` `/sessions` `/branch` `/summary` `/plan` `/tool-approval-mode` `/goal` `/delete-session` 等，斜杠命令一律走 `/submit`（如 `/model <ref>`）。

## 10.3 对 Phase 4 的落地要求

- Android 客户端传输实现（OkHttp EventSource）：① 先抓 Cookie → ② `/events` SSE（忽略 `: connected`/`: ping`，`data:` 帧剥前缀交给 `ReasonixEvent.fromLine`）→ ③ `POST /submit` 同 Cookie。
- 任务卡片收尾渲染要同时容忍 `result` 与 `turn_done` 两种事件。
- 会话复用：Gateway 按 Skill 分组复用同一 serve 会话（缓存友好）；跨模型切换走 `/submit` 斜杠命令 `/model <ref>`。

*上一份：`09-Phase3-AresGateway验收.md`*