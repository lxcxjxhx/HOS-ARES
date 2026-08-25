# 09 · Phase 3 验收：AresGateway 网关（serve 通道实测，2026-08-25）

## 9.1 验收结论 ✅（骨架 + 首选通道协议实测）

| 验收项 | 结果 | 证据 |
|--------|------|------|
| serve 服务起服 | ✅ | `reasonix serve --addr 127.0.0.1:8931 --auth token` 正常监听，控制台打印 share 链接与余额 `¥45.19` |
| Token 鉴权基线 | ✅ 实测 | 无 token GET `/` → **401**；带 token `/?token=` → **200**（Reasonix 分享页） |
| **鉴权机制** | ✅ 实测（方案修正点） | **Cookie 制**：`GET /?token=<T>` → `Set-Cookie: reasonix_token=<T>`（HttpOnly）；**非 Bearer 头**（Bearer 探测返回 401/Unauthorized） |
| SSE 端点 | ✅ 实测（方案修正点） | 真实端点为 **`/events`**（不是 /sse；/sse 302 → /events），带 Cookie 请求首帧即 `: connected`（SSE 心跳注释帧），后随 `data: <json-line>` 事件帧 |
| 任务提交端点 | ⏳ 待确认 | 按 Streamable HTTP 惯例为 `POST /messages?session_id=<id>`；精确端点以 serve 页面 JS 为准（Phase 4 Android 客户端实测确认） |
| Kotlin 骨架 | ✅ 落盘 | 8 文件：TaskCard 状态机 / ReasonixEvent 解析 / Skill+SkillRegistry 路由 / ReasonixClient 双通道 / HttpStreams+ProcessStreams 传输 / AresGateway 门面；已修正编译问题（生命周期字段 var、flow.collect 导入、按实测协议重构 SSE 桩） |

## 9.2 实测 serve 接线协议（AresGateway 主通道蓝图）

```
Android AresGateway (HttpSseTransport)
  ① GET {base}/?token=<T>            → 200 + Set-Cookie: reasonix_token=<T>（抓 Cookie）
  ② GET {base}/events  (Cookie)      → SSE：首帧 ": connected"，随后 "data: <json-line>"
       data 帧剥离 "data: " 前缀 → ReasonixEvent.fromLine → TaskCard 状态机
  ③ POST {base}/messages?session_id=<id>  → 提交任务（Streamable HTTP，端点以页面 JS 为准）
```

## 9.3 与 07 号文偏差表的衔接

- 07 号文修正点 4（serve 通道）→ 本轮补充实测细节：鉴权为 **Cookie 而非 Bearer**、SSE 端点为 **/events 而非 /sse**。AresGateway HttpStreams 桩已按此协议重写。

## 9.4 下一步

- **Phase 4（Android UI）**：OkHttp + okhttp-sse 实现 HttpStreams 真实传输（Cookie 握手 → /events 建链 → data 帧解析），Compose 任务卡片流式渲染。
- 开发机无 Gradle，Kotlin 编译验证推迟至 Phase 5 打包环境；当前以协议实测 + 骨架一致性为准。