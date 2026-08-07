package com.hos.ares

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.withContext
import java.io.BufferedReader
import java.io.InputStreamReader

/**
 * HOS-ARES 统一 Agent 入口（Reasonix 网关，原创实现）
 *
 * 职责：
 *   1. 任务识别（SkillRegistry）
 *   2. 按工作流调度技能插件（analyze -> verify -> report）
 *   3. 通过 ProotRuntime 在 Android 上的 Alpine Linux 中执行真实 agent 命令
 *   4. 流式输出执行结果
 */
class ReasonixGateway(
    private val proot: ProotRuntime,
    private val envProvider: () -> Map<String, String> = { emptyMap() },
) {

    private val _output = MutableStateFlow("")
    val output: StateFlow<String> = _output

    private val _running = MutableStateFlow(false)
    val running: StateFlow<Boolean> = _running

    private val _events = MutableStateFlow<List<AgentRunEvent>>(emptyList())
    val events: StateFlow<List<AgentRunEvent>> = _events

    suspend fun run(task: String): Result<String> = withContext(Dispatchers.IO) {
        _running.value = true
        _output.value = ""
        _events.value = emptyList()
        try {
            val skills = SkillRegistry.recognize(task)
            append("HOS: 已识别任务，调度 $${skills.size} 个技能\n")
            for (skill in skills) {
                append("→ 调用 ${skill.name}${if (skill.requiresLl) " [需 LLM]" else ""}\n")
                _events.value = _events.value + AgentRunEvent(skill.name, AgentStatus.PENDING, "", skill.requiresLl)
                update(skill.name) { it.copy(status = AgentStatus.RUNNING, detail = "开始执行 ${skill.name}…\n") }
                when (val r = proot.runAgent(skill.name, task, envProvider())) {
                    is Result.Success -> {
                        append("  ✓ ${skill.name} 完成: ${r.value}\n")
                        update(skill.name) { it.copy(status = AgentStatus.DONE, detail = it.detail + "✓ 完成: ${r.value}\n") }
                    }
                    is Result.Failure -> {
                        append("  ✗ ${skill.name}: ${r.error}\n")
                        update(skill.name) { it.copy(status = AgentStatus.FAILED, detail = it.detail + "✗ 失败: ${r.error}\n") }
                    }
                }
            }
            append("\nHOS: 报告生成完毕。")
            Result.Success(_output.value)
        } catch (e: Exception) {
            append("\nHOS: 出错 ${e.message}")
            Result.Failure(e.message ?: "unknown")
        } finally {
            _running.value = false
        }
    }

    private fun append(s: String) {
        _output.value = _output.value + s
    }

    /** 按 skill 更新/追加对应 Agent 事件。 */
    private fun update(skill: String, transform: (AgentRunEvent) -> AgentRunEvent) {
        val list = _events.value
        val idx = list.indexOfFirst { it.skill == skill }
        val base = if (idx >= 0) list[idx] else AgentRunEvent(skill, AgentStatus.PENDING, "")
        val newItem = transform(base)
        _events.value = if (idx >= 0) list.toMutableList().also { it[idx] = newItem } else list + newItem
    }
}

sealed class Result<out T> {
    data class Success<out T>(val value: T) : Result<T>()
    data class Failure(val error: String) : Result<Nothing>()
}
