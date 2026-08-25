package com.hos.ares.gateway

import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow

/**
 * SSE 流实现桩（reasonix serve HTTP+SSE 通道）。
 * Phase 4/5：接入 OkHttp + okhttp-sse（Android）或 java.net.http.HttpClient（JVM）。
 * 协议要点：GET {base}/sse，Authorization: Bearer <token>，服务端推 events;data=JSON，
 * 首事件含 endpoint（POST 会话端点），后续为 stream-json 事件行。
 */
object HttpStreams {
    private val active = java.util.concurrent.atomic.AtomicReference<AutoCloseable?>()

    fun sse(
        baseUrl: String,    // 形如 http://127.0.0.1:8931 —— 鉴权经 Cookie，不走 Bearer 头（实测，见 docs/ares-gateway-设计.md §7）
        token: String,      // reasonix serve --auth token 的 token 值
        onSession: (String) -> Unit,
    ): Flow<ReasonixEvent> = flow {
        // 实测服务端协议（reasonix serve v1.19.1, 2026-08-25）：
        //   1) GET {base}/?token=<T>            → 200 HTML + Set-Cookie: reasonix_token=<T>（HttpOnly）
        //      根路径无 token 时 → 401（鉴权基线已验证）
        //   2) GET {base}/events                → 带 Cookie；首帧 `: connected`（SSE 注释/心跳帧）
        //      后续事件帧 `data: <json-line>` → 剥离 "data: " 前缀后交 ReasonixEvent.fromLine 解析
        //      （/sse 为旧路径，302 → /events）
        //   3) 任务提交：POST {base}/messages?session_id=<id>（Streamable HTTP；
        //      精确端点以 serve 页面 JS 为准，Phase 4 Android 客户端实现时确认）
        // TODO(Phase 4): OkHttp 实现——第一步抓 Set-Cookie；随后 EventSource 携带 Cookie，
        //   data 帧逐行 parse 并 emit。
        onSession("<cookie-session-id>")
        emit(ReasonixEvent.TurnStarted())
    }

    fun cancel() {
        active.getAndSet(null)?.close()
    }
}