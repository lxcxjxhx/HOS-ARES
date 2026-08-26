package com.hos.ares.gateway

/** 任务卡片状态机（依据 reasonix 实测事件流设计） */
enum class TaskStatus {
    PENDING,   // 已提交，等待执行
    RUNNING,   // 回合进行中（含 RETRY 角标）
    TOOL,      // 正在调用工具（MCP / bash）
    DONE,      // 成功（收到 result 事件）
    FAILED,    // 失败
    CANCELLED; // 用户取消

    val isTerminal: Boolean get() = this == DONE || this == FAILED || this == CANCELLED
}

data class TaskCard(
    val id: String,
    val skillId: String,
    val input: String,
    var status: TaskStatus = TaskStatus.PENDING,
    val reasoning: StringBuilder = StringBuilder(),
    val output: StringBuilder = StringBuilder(),
    val tools: MutableList<ToolCall> = mutableListOf(),
    val cost: CostBreakdown = CostBreakdown(),
    var sessionId: String? = null,
    var error: String? = null,
    val createdAt: Long = System.currentTimeMillis(),
    var startedAt: Long? = null,
    var finishedAt: Long? = null,
)

data class ToolCall(
    val name: String,       // 如 mcp__hos-ares-demo__demo_add
    val arguments: String,  // JSON 参数
    var result: String? = null,
    var durationMs: Long? = null,
    val readOnly: Boolean = true,
)

/** 成本与缓存统计（来自 usage 事件） */
data class CostBreakdown(
    var promptTokens: Long = 0,
    var completionTokens: Long = 0,
    var cacheHitTokens: Long = 0,
    var cacheMissTokens: Long = 0,
    var totalCostCny: Double = 0.0,
) {
    val cacheHitRate: Double
        get() = if (promptTokens + completionTokens == 0L) 0.0
        else (cacheHitTokens.toDouble() / (promptTokens).toDouble())
}