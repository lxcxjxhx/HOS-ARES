package com.hos.ares

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hos.ares.gateway.AresGateway
import com.hos.ares.gateway.ReasonixEvent
import com.hos.ares.gateway.TaskCard
import com.hos.ares.gateway.TaskStatus
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * UI 状态层：持有任务卡片流，封装 AresGateway 的提交/取消/事件映射。
 * Phase 5 接入真实传输时，AresGateway 构造参数改为 serve 连接信息
 * （{baseUrl, token}，Termux 内 127.0.0.1:8931 + 项目 token）。
 */
class AresViewModel(
    private val gateway: AresGateway = AresGateway(), // TODO(Phase 5): 注入 HttpSseTransport
) : ViewModel() {

    private val _cards = MutableStateFlow<List<TaskCard>>(emptyList())
    val cards: StateFlow<List<TaskCard>> = _cards.asStateFlow()

    private val _busy = MutableStateFlow(false)
    val busy: StateFlow<Boolean> = _busy.asStateFlow()

    fun submit(input: String) {
        if (input.isBlank()) return
        viewModelScope.launch {
            _busy.value = true
            val card = gateway.submit(input, viewModelScope)
            _cards.value += card
            _busy.value = false
        }
    }

    fun cancel(id: String) {
        gateway.cancel(id)
        _cards.value = _cards.value.map {
            if (it.id == id && !it.status.isTerminal) it.copy(status = TaskStatus.CANCELLED) else it
        }
    }

    /** 事件回调挂接点：Phase 5 真实传输完成前由 Gateway 内部直接更新卡片；此处保留扩展位。 */
    internal fun onEvent(cardId: String, event: ReasonixEvent) {
        // 后续可在此做卡片快照推送（通知/角标）
    }
}