package com.hos.ares.gateway

import java.util.concurrent.ConcurrentHashMap
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.launch

/**
 * AresGateway 门面（L2）：
 * submit → SkillRegistry 路由 → TaskCard 创建 → reasonix 事件流 → 状态机流转 → 成本聚合。
 * 事件→状态映射依据 reasonix stream-json 实测事件 kind（见 ReasonixEvent）。
 */
class AresGateway(
    private val registry: SkillRegistry = SkillRegistry(),
    private val transport: ReasonixTransport = HttpSseTransport(token = ""), // token 由配置注入
) {
    private val cards = ConcurrentHashMap<String, TaskCard>()
    private val jobs = ConcurrentHashMap<String, Job>()

    fun submit(input: String, scope: CoroutineScope): TaskCard {
        val skill = registry.classify(input)
        val card = TaskCard(
            id = java.util.UUID.randomUUID().toString(),
            skillId = skill.id,
            input = input,
        )
        cards[card.id] = card

        val job = scope.launch {
            transport.stream(input, skill) { sessionId ->
                synchronized(card) { card.sessionId = sessionId } // 前缀缓存会话复用
            }.collect { event -> apply(card, event) }
        }
        jobs[card.id] = job
        return card
    }

    fun card(id: String): TaskCard? = cards[id]

    fun cancel(id: String) {
        jobs.remove(id)?.cancel()
        transport.cancel()
        cards[id]?.let { synchronized(it) { it.status = TaskStatus.CANCELLED; it.finishedAt = System.currentTimeMillis() } }
    }

    private fun apply(card: TaskCard, event: ReasonixEvent) {
        synchronized(card) {
            when (event) {
                is ReasonixEvent.TurnStarted -> {
                    if (card.status == TaskStatus.PENDING) { card.status = TaskStatus.RUNNING; card.startedAt = System.currentTimeMillis() }
                }
                is ReasonixEvent.Retrying -> card.status = TaskStatus.RUNNING
                is ReasonixEvent.Reasoning -> { card.reasoning.append(event.text).append('\n') }
                is ReasonixEvent.Text -> card.output.append(event.text)
                is ReasonixEvent.Message -> { card.output.append(event.text); if (!event.reasoning.isNullOrBlank()) card.reasoning.append(event.reasoning).append('\n') }
                is ReasonixEvent.ToolDispatch -> {
                    card.status = TaskStatus.TOOL
                    card.tools.add(ToolCall(name = event.name, arguments = event.arguments, readOnly = event.readOnly))
                }
                is ReasonixEvent.ToolResult -> {
                    val tool = card.tools.lastOrNull()
                    if (tool != null && tool.name == event.name) { tool.result = event.output; tool.durationMs = event.durationMs }
                    card.status = TaskStatus.RUNNING
                }
                is ReasonixEvent.Usage -> {
                    card.cost.promptTokens += event.promptTokens
                    card.cost.completionTokens += event.completionTokens
                    card.cost.cacheHitTokens = event.sessionCacheHitTokens
                    card.cost.cacheMissTokens = event.sessionCacheMissTokens
                    card.cost.totalCostCny += event.costCny
                }
                is ReasonixEvent.Result -> {
                    card.sessionId = event.sessionId ?: card.sessionId
                    card.status = if (event.isError) TaskStatus.FAILED else TaskStatus.DONE
                    if (event.isError) card.error = event.result
                    else card.output.append(event.result)
                    card.finishedAt = System.currentTimeMillis()
                }
                is ReasonixEvent.TurnDone -> {
                    // serve 通道收尾：usage 已累计成本，此处补 DONE（无 result 帧时）
                    if (!card.status.isTerminal) {
                        card.status = TaskStatus.DONE
                        card.finishedAt = System.currentTimeMillis()
                    }
                }
            }
        }
    }
}