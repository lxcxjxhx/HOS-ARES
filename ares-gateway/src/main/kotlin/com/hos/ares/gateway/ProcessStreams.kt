package com.hos.ares.gateway

import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow

/**
 * 子进程流实现桩（Termux proot 内运行 reasonix：-p / run）。
 * Phase 4/5：Android 端 ProcessBuilder 启动 proot → reasonix CLI，逐行读取 stdout，
 * 调用 parse(line)（即 ReasonixEvent.fromLine）转发事件；cancel 时 destroy 进程。
 */
object ProcessStreams {
    private val running = java.util.concurrent.atomic.AtomicReference<Process?>()

    fun lines(
        args: List<String>,
        parse: (String) -> ReasonixEvent?,
        onSession: (String) -> Unit,
    ): Flow<ReasonixEvent> = flow {
        // TODO(Phase 4): ProcessBuilder(args).start() → bufferedReader().forEachLine { parse(it)?.let(::emit) }
        onSession("<subprocess-session-id>")
        emit(ReasonixEvent.TurnStarted())
    }

    fun cancel() {
        running.getAndSet(null)?.destroy()
    }
}