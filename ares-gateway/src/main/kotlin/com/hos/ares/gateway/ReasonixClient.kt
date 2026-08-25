package com.hos.ares.gateway

import kotlinx.coroutines.flow.Flow

/**
 * reasonix 传输抽象。
 * 首选 HttpSseTransport（reasonix serve，HTTP+SSE，token 鉴权，长会话缓存复用）；
 * 备选 SubprocessTransport（Termux 内 proot 子进程：reasonix run --events-jsonl / -p --output-format stream-json）。
 */
interface ReasonixTransport {
    fun stream(task: String, skill: Skill, onSession: (String) -> Unit = {}): Flow<ReasonixEvent>
    fun cancel()
}

/** serve 通道：reasonix serve --addr 127.0.0.1:8931 --auth token --token <T> */
class HttpSseTransport(
    private val baseUrl: String = "http://127.0.0.1:8931",
    private val token: String,
    private val client: SseHttpClient = SseHttpClient(),
) : ReasonixTransport {

    override fun stream(task: String, skill: Skill, onSession: (String) -> Unit): Flow<ReasonixEvent> =
        client.streamSse(baseUrl = baseUrl, token = token, onSession = onSession)

    override fun cancel() = client.cancel()

    private fun buildRequest(task: String, skill: Skill): Map<String, Any> = mapOf(
        "task" to task,
        "model" to skill.model,
        // 会话复用：同一 Skill 共享会话 id，最大化 DeepSeek 前缀缓存命中
        "session" to skill.session,
        "output_format" to "stream-json",
    )
}

/** 子进程通道（Termux proot 内）：reasonix run <task> --model <m> --output-format stream-json */
class SubprocessTransport(
    private val prootLogin: List<String> = listOf("proot", "-0", "-r", "/data/data/com.termux/files/usr/var/lib/proot-distro/installed-rootfs/alpine"),
    private val runner: CommandRunner = CommandRunner(),
) : ReasonixTransport {

    override fun stream(task: String, skill: Skill, onSession: (String) -> Unit): Flow<ReasonixEvent> {
        val args = prootLogin + listOf(
            "/bin/sh", "-c",
            "cd ~/hos-ares && reasonix -p \"$task\" --model ${skill.model} --output-format stream-json",
        )
        return runner.runStreaming(args, ::parseLine, onSession)
    }

    override fun cancel() = runner.cancel()

    private fun parseLine(line: String): ReasonixEvent? = ReasonixEvent.fromLine(line)

    private inline fun <T> dummy(x: T): T = x // placeholder to keep file self-contained
}

/** SSE 客户端（实现见 AresGateway.kt 配套类；Android 端可用 OkHttp + EventSource） */
class SseHttpClient {
    fun streamSse(baseUrl: String, token: String, onSession: (String) -> Unit): Flow<ReasonixEvent> =
        HttpStreams.sse(baseUrl, token, onSession)

    fun cancel() = HttpStreams.cancel()
}

/** 命令运行器（Termux 子进程；Android 端用 ProcessBuilder / Runtime.exec） */
class CommandRunner {
    fun runStreaming(args: List<String>, parse: (String) -> ReasonixEvent?, onSession: (String) -> Unit): Flow<ReasonixEvent> =
        ProcessStreams.lines(args, parse, onSession)

    fun cancel() = ProcessStreams.cancel()
}