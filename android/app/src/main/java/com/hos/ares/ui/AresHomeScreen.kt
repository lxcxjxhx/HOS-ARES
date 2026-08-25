package com.hos.ares.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.hos.ares.AresViewModel
import com.hos.ares.gateway.TaskCard
import com.hos.ares.gateway.TaskStatus
import com.hos.ares.gateway.ToolCall

/**
 * 对话式主界面（Phase 4 骨架）：任务卡片流（PENDING→RUNNING/TOOL→DONE/FAILED）
 * + 输入框；卡片内展示 状态徽章/推理区/正文流/工具调用/成本行。
 */
@Composable
fun AresHomeScreen(vm: AresViewModel) {
    var input by remember { mutableStateOf("") }
    val cards by vm.cards.collectAsState()
    val busy by vm.busy.collectAsState()

    Column(Modifier.fillMaxSize().padding(12.dp)) {
        Text("HOS-ARES · 移动安全审计 Agent", style = MaterialTheme.typography.titleMedium)
        LazyColumn(
            Modifier.weight(1f).fillMaxWidth(),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            items(cards, key = { it.id }) { TaskCardView(it) { vm.cancel(it.id) } }
        }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(
                value = input,
                onValueChange = { input = it },
                modifier = Modifier.weight(1f),
                placeholder = { Text("如：对 sample.apk 做静态分析 / frida 插桩某App / 审计依赖漏洞") },
                enabled = !busy,
            )
            Button(
                onClick = { vm.submit(input); input = "" },
                enabled = input.isNotBlank() && !busy,
            ) { Text(if (busy) "执行中…" else "执行") }
        }
    }
}

@Composable
fun TaskCardView(card: TaskCard, onCancel: () -> Unit) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp)) {
            Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                StatusBadge(card.status)
                Text(
                    "  ${card.skillId}",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.primary,
                )
            }
            Text(card.input, style = MaterialTheme.typography.bodyMedium)
            card.error?.let { Text("错误：$it", color = MaterialTheme.colorScheme.error) }

            if (card.reasoning.isNotBlank()) {
                Text(
                    "思考：${card.reasoning}",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            if (card.output.isNotBlank()) {
                Text(card.output.toString(), style = MaterialTheme.typography.bodyMedium)
            }
            card.tools.forEach { ToolRow(it) }

            // 成本行
            if (card.cost.promptTokens > 0) {
                Text(
                    "tokens ${card.cost.promptTokens}+${card.cost.completionTokens} · 缓存 ${card.cost.cacheHitTokens} (~${"%.1f".format(card.cost.cacheHitRate * 100)}%) · ¥${"%.4f".format(card.cost.totalCostCny)}",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.outline,
                )
            }
            if (!card.status.isTerminal) {
                TextButton(onClick = onCancel) { Text("取消") }
            }
        }
    }
}

@Composable
private fun StatusBadge(status: TaskStatus) {
    val (label, color) = when (status) {
        TaskStatus.PENDING -> "排队中" to MaterialTheme.colorScheme.outline
        TaskStatus.RUNNING -> "执行中" to MaterialTheme.colorScheme.primary
        TaskStatus.TOOL -> "调工具" to MaterialTheme.colorScheme.tertiary
        TaskStatus.DONE -> "完成" to MaterialTheme.colorScheme.primary
        TaskStatus.FAILED -> "失败" to MaterialTheme.colorScheme.error
        TaskStatus.CANCELLED -> "已取消" to MaterialTheme.colorScheme.outline
    }
    AssistChip(onClick = {}, label = { Text(label) })
}

@Composable
private fun ToolRow(tool: ToolCall) {
    Text(
        "⚙ ${tool.name} ${tool.arguments.take(80)}${if (tool.arguments.length > 80) "…" else ""}",
        style = MaterialTheme.typography.labelSmall,
        color = MaterialTheme.colorScheme.tertiary,
    )
    tool.result?.let { Text(it.take(200), style = MaterialTheme.typography.labelSmall) }
}