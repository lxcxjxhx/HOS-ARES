package com.hos.ares

/** 单个 Agent 的运行状态。 */
enum class AgentStatus { PENDING, RUNNING, DONE, FAILED, CANCELLED, TIMEOUT }

/** Codex/Claude 式结构化执行事件：一次任务 = 一组 Agent 运行卡片 + 详细流。 */
data class AgentRunEvent(
    val skill: String,            // Agent/技能名
    val status: AgentStatus,      // 运行状态
    val detail: String,           // 该 Agent 的流式详细输出（累积）
    val requiresLl: Boolean = false,
) {
    val isTerminal: Boolean get() = status == AgentStatus.DONE || status == AgentStatus.FAILED || status == AgentStatus.CANCELLED || status == AgentStatus.TIMEOUT
}
