package com.hos.ares.gateway

import org.json.JSONObject

/**
 * reasonix stream-json / events-jsonl 事件模型（实测 kind 集合，v1.19.1）：
 * turn_started | retrying | reasoning | tool_dispatch | tool_result | text | message | usage | result
 */
sealed class ReasonixEvent {
    abstract val kind: String

    data class TurnStarted(override val kind: String = "turn_started") : ReasonixEvent()
    data class Retrying(val retryAttempt: Int, val retryMax: Int) : ReasonixEvent() {
        override val kind: String = "retrying"
    }
    data class Reasoning(val text: String) : ReasonixEvent() {
        override val kind: String = "reasoning"
    }
    data class ToolDispatch(
        val id: String,
        val name: String,        // 如 mcp__hos-ares-demo__demo_add
        val arguments: String,
        val readOnly: Boolean,
        val partial: Boolean = false,
    ) : ReasonixEvent() {
        override val kind: String = "tool_dispatch"
    }
    data class ToolResult(
        val id: String,
        val name: String,
        val output: String,
        val durationMs: Long?,
        val readOnly: Boolean,
    ) : ReasonixEvent() {
        override val kind: String = "tool_result"
    }
    data class Text(val text: String) : ReasonixEvent() {
        override val kind: String = "text"
    }
    data class Message(val text: String, val reasoning: String? = null) : ReasonixEvent() {
        override val kind: String = "message"
    }
    data class Usage(
        val promptTokens: Long,
        val completionTokens: Long,
        val cacheHitTokens: Long,
        val cacheMissTokens: Long,
        val sessionCacheHitTokens: Long,
        val sessionCacheMissTokens: Long,
        val costCny: Double,
    ) : ReasonixEvent() {
        override val kind: String = "usage"
    }
    data class Result(
        val result: String,
        val sessionId: String?,
        val isError: Boolean,
        val durationMs: Long?,
        val totalCostCny: Double?,
        val numTurns: Int?,
    ) : ReasonixEvent() {
        override val kind: String = "result"
    }

    /**
     * serve 通道的回合收尾事件（实测，v1.19.1）：`-p` 模式以 `result` 收尾，
     * serve /events 通道以 `usage` + `turn_done` 收尾（无 result 帧）。
     * AresGateway 状态机需同时处理两种收尾。
     */
    data class TurnDone(val reason: String? = null) : ReasonixEvent() {
        override val kind: String = "turn_done"
    }

    companion object {
        /** 解析单行 JSON 事件；无法识别时返回 null */
        fun fromLine(line: String): ReasonixEvent? {
            val j = runCatching { JSONObject(line) }.getOrNull() ?: return null
            return when (j.optString("kind", "")) {
                "turn_started" -> TurnStarted()
                "retrying" -> Retrying(j.optInt("retryAttempt", 0), j.optInt("retryMax", 0))
                "reasoning" -> Reasoning(j.optString("text", ""))
                "text" -> Text(j.optString("text", ""))
                "message" -> Message(j.optString("text", ""), j.optString("reasoning", ""))
                "tool_dispatch" -> {
                    val t = j.optJSONObject("tool") ?: return null
                    ToolDispatch(
                        id = t.optString("id"),
                        name = t.optString("name"),
                        arguments = t.optString("args", ""),
                        readOnly = t.optBoolean("readOnly", true),
                        partial = t.optBoolean("partial", false),
                    )
                }
                "tool_result" -> {
                    val t = j.optJSONObject("tool") ?: return null
                    ToolResult(
                        id = t.optString("id"),
                        name = t.optString("name"),
                        output = t.optString("output", ""),
                        durationMs = if (t.has("durationMs")) t.optLong("durationMs") else null,
                        readOnly = t.optBoolean("readOnly", true),
                    )
                }
                "usage" -> {
                    val u = j.optJSONObject("usage") ?: return null
                    Usage(
                        promptTokens = u.optLong("promptTokens", 0),
                        completionTokens = u.optLong("completionTokens", 0),
                        cacheHitTokens = u.optLong("cacheHitTokens", 0),
                        cacheMissTokens = u.optLong("cacheMissTokens", 0),
                        sessionCacheHitTokens = u.optLong("sessionCacheHitTokens", 0),
                        sessionCacheMissTokens = u.optLong("sessionCacheMissTokens", 0),
                        costCny = u.optDouble("cost", 0.0),
                    )
                }
                "turn_done" -> TurnDone(j.optString("reason", null).ifEmpty { null })
                "result" -> Result(
                    result = j.optString("result", ""),
                    sessionId = j.optString("session_id", null).ifEmpty { null },
                    isError = j.optBoolean("is_error", false),
                    durationMs = if (j.has("duration_ms")) j.optLong("duration_ms") else null,
                    totalCostCny = if (j.has("total_cost")) j.optDouble("total_cost") else null,
                    numTurns = if (j.has("num_turns")) j.optInt("num_turns") else null,
                )
                else -> null
            }
        }
    }
}