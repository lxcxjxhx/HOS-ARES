package com.hos.ares

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update

/**
 * HOS-ARES 统一 Agent 入口（Ares 网关，原创实现）
 *
 * 职责：
 *   1. 任务识别（SkillRegistry）
 *   2. 并行调度技能插件（analyze -> verify -> report）
 *   3. 通过 ProotRuntime 在 Android 上的 Alpine Linux 中执行真实 agent 命令
 *   4. 流式输出执行结果，支持取消与超时
 */
class AresGateway(
    private val proot: ProotRuntime,
    private val envProvider: () -> Map<String, String> = { emptyMap() },
) {

    private val _output = MutableStateFlow("")
    val output: StateFlow<String> = _output

    private val _running = MutableStateFlow(false)
    val running: StateFlow<Boolean> = _running

    private val _events = MutableStateFlow<List<AgentRunEvent>>(emptyList())
    val events: StateFlow<List<AgentRunEvent>> = _events

    suspend fun run(task: String, projectDir: String?, timeoutMillis: Long = 0): Result<String> {
        _running.value = true
        _output.value = ""
        _events.value = emptyList()
        return try {
            val skills = SkillRegistry.recognize(task)
            _output.update { it + "HOS: 已识别任务，调度 ${skills.size} 个技能\n" }
            for (skill in skills) {
                _events.update { it + AgentRunEvent(skill.name, AgentStatus.PENDING, "", skill.requiresLl) }
            }

            // 统一入口：reasonix 优先（为其建立 RUNNING 卡片）
            val reasonixName = "reasonix"
            _events.update { it + AgentRunEvent(reasonixName, AgentStatus.RUNNING, "", requiresLl = true) }
            val reasonixResult = runReasonix(task, projectDir, timeoutMillis)

            if (reasonixResult is Result.Success) {
                // reasonix 成功 → 直接进入汇总，reasonix 卡片置为 DONE
                setStatus(reasonixName, AgentStatus.DONE, "✓ 统一入口执行完成\n")
            } else {
                // reasonix 失败：如实报告（rootfs 无其它 agent 的 run.sh，直连回退不可用）
                val err = (reasonixResult as? Result.Failure)?.error ?: "未知错误"
                setStatus(reasonixName, AgentStatus.FAILED, "✗ $err\n")
                _output.update { it + "\nHOS: reasonix 统一入口失败：$err\n" }
            }

            _output.update { it + "\n" }
            val summary = summarize()
            _output.update { it + summary }
            _output.update { it + "\nHOS: 报告生成完毕。" }
            Result.Success(_output.value)
        } catch (e: CancellationException) {
            _output.update { it + "\nHOS: 任务已取消。" }
            throw e
        } catch (e: Exception) {
            _output.update { it + "\nHOS: 出错 ${e.message}" }
            Result.Failure(e.message ?: "unknown")
        } finally {
            _running.value = false
        }
    }

    /** 调用 reasonix 统一入口；其流式输出经 onOutput 路由到事件解析。 */
    private suspend fun runReasonix(
        task: String,
        projectDir: String?,
        timeoutMillis: Long,
    ): Result<String> {
        return proot.runAgent(
            "reasonix", task, projectDir, envProvider(), timeoutMillis,
            onOutput = { line -> handleReasonixOutput(line) },
        )
    }

    /**
     * 处理 reasonix 统一入口的流式输出：
     * 1. 追加到 reasonix 卡片与整体输出；
     * 2. 解析其中的 HOS-SKILL:<name>:<RUNNING|DONE|FAILED> 标记，更新对应技能事件状态。
     */
    private fun handleReasonixOutput(line: String) {
        append(line)
        _events.update { list ->
            val idx = list.indexOfFirst { it.skill == "reasonix" }
            if (idx >= 0) {
                list.toMutableList().also { it[idx] = it[idx].copy(detail = it[idx].detail + line) }
            } else {
                list + AgentRunEvent("reasonix", AgentStatus.RUNNING, line, true)
            }
        }
        parseSkillMarkers(line)
    }

    /** 解析 reasonix 输出的技能状态标记行，据此更新各技能 AgentRunEvent 状态（保持 detail 追加）。 */
    private fun parseSkillMarkers(line: String) {
        val trimmed = line.trim()
        if (!trimmed.startsWith("HOS-SKILL:")) return
        val parts = trimmed.removePrefix("HOS-SKILL:").split(":")
        if (parts.size < 2) return
        val name = parts[0].trim()
        val status = when (parts[1].trim().uppercase()) {
            "RUNNING" -> AgentStatus.RUNNING
            "DONE" -> AgentStatus.DONE
            "FAILED" -> AgentStatus.FAILED
            else -> return
        }
        setStatus(name, status, "")
    }

    /** 按 skill 更新对应事件状态并追加 detail。 */
    private fun setStatus(skill: String, status: AgentStatus, detailAppend: String) {
        _events.update { list ->
            val idx = list.indexOfFirst { it.skill == skill }
            val base = if (idx >= 0) list[idx] else AgentRunEvent(skill, AgentStatus.PENDING, "")
            val newItem = base.copy(status = status, detail = base.detail + detailAppend)
            if (idx >= 0) list.toMutableList().also { it[idx] = newItem } else list + newItem
        }
    }

    /** 仅更新 _output。 */
    private fun append(s: String) {
        _output.update { it + s }
    }

    /** 根据 _events 当前终态生成结构化汇总。 */
    private fun summarize(): String {
        val evs = _events.value
        val done = evs.count { it.status == AgentStatus.DONE }
        val failed = evs.count { it.status == AgentStatus.FAILED }
        val timeout = evs.count { it.status == AgentStatus.TIMEOUT }
        val cancelled = evs.count { it.status == AgentStatus.CANCELLED }
        return "HOS: 汇总 — 完成: $done · 失败: $failed · 超时: $timeout · 取消: $cancelled"
    }
}

sealed class Result<out T> {
    data class Success<out T>(val value: T) : Result<T>()
    data class Failure(val error: String) : Result<Nothing>()
}
