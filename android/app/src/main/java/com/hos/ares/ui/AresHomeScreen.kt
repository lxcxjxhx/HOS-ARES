package com.hos.ares.ui

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.hos.ares.AresViewModel
import com.hos.ares.gateway.TaskCard
import com.hos.ares.gateway.TaskStatus
import com.hos.ares.gateway.ToolCall
import com.hos.ares.ui.theme.*

/**
 * HOS-ARES · Ares-V3 Neon 主界面（Phase 4 炫酷版）
 * 视觉语言：深紫黑底 + 渐变霓虹标题 + 玻璃任务卡（渐变描边）+ 扫描线 + 状态呼吸灯 + 霓虹输入区。
 * 与 Phase 4 骨架契约不变：cards/submit/cancel 接口保持（vm.cards / vm.busy / vm.submit / vm.cancel）。
 * 零外部依赖：仅 Compose 基础 API + Material3 + 动画核心。
 */
@Composable
fun AresHomeScreen(vm: AresViewModel) {
    var input by remember { mutableStateOf("") }
    val cards by vm.cards.collectAsState()
    val busy by vm.busy.collectAsState()

    Box(
        Modifier
            .fillMaxSize()
            .background(BgInk)
    ) {
        // 顶部品牌光晕（电光紫 → 透明）+ 底部青色微光
        Box(
            Modifier
                .fillMaxWidth()
                .height(220.dp)
                .background(Brush.verticalGradient(listOf(NeonPurple.copy(alpha = 0.26f), Color.Transparent)))
        )
        Box(
            Modifier
                .align(Alignment.BottomCenter)
                .fillMaxWidth()
                .height(180.dp)
                .background(Brush.verticalGradient(listOf(Color.Transparent, NeonCyan.copy(alpha = 0.14f))))
        )

        Column(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
            Header(busy)
            Spacer(Modifier.height(8.dp))
            ScanlineOverlay { TaskList(cards) { vm.cancel(it) } }
            Spacer(Modifier.height(10.dp))
            InputBar(input, busy, onChange = { input = it }, onRun = {
                if (input.isNotBlank()) {
                    vm.submit(input.trim())
                    input = ""
                }
            })
            Spacer(Modifier.height(14.dp))
        }
    }
}

@Composable
private fun Header(busy: Boolean) {
    Row(
        Modifier.fillMaxWidth().padding(top = 22.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        BrandDot(busy)
        Spacer(Modifier.width(10.dp))
        Column {
            Text(
                "HOS-ARES",
                style = TextStyle(
                    brush = GradTitle,
                    fontWeight = FontWeight.Black,
                    fontSize = 26.sp,
                    letterSpacing = 2.sp,
                ),
            )
            Text(
                "移动渗透作战室 · reasonix Agent",
                style = MaterialTheme.typography.labelMedium,
                color = TextLo,
            )
        }
    }
}

/** 品牌呼吸灯：空闲=青常亮（微呼吸），执行=紫脉冲 */
@Composable
private fun BrandDot(busy: Boolean) {
    val transition = rememberInfiniteTransition(label = "brand")
    val pulse by transition.animateFloat(
        initialValue = 0.35f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(
            tween(if (busy) 520 else 1400, easing = LinearEasing),
            RepeatMode.Reverse,
        ),
        label = "pulse",
    )
    Box(Modifier.size(26.dp), contentAlignment = Alignment.Center) {
        Box(
            Modifier
                .size(14.dp)
                .background(if (busy) NeonMagenta else NeonCyan, CircleShape)
                .alpha(pulse)
        )
    }
}

/** 扫描线容器：任务列表之上循环扫过一条电光青细线（weight 需 ColumnScope 接收者） */
@Composable
private fun ColumnScope.ScanlineOverlay(content: @Composable () -> Unit) {
    Box(Modifier.fillMaxWidth().weight(1f)) {
        content()
        val transition = rememberInfiniteTransition(label = "scan")
        val scanY by transition.animateFloat(
            initialValue = 0f,
            targetValue = 1f,
            animationSpec = infiniteRepeatable(
                tween(3000, easing = LinearEasing),
                RepeatMode.Restart,
            ),
            label = "scanY",
        )
        Box(
            Modifier
                .fillMaxWidth()
                .height(2.dp)
                .graphicsLayer { translationY = size.height * scanY }
                .padding(horizontal = 4.dp)
                .background(NeonCyan.copy(alpha = 0.65f), CircleShape)
        )
    }
}

@Composable
private fun TaskList(cards: List<TaskCard>, onCancel: (String) -> Unit) {
    if (cards.isEmpty()) {
        EmptyState()
        return
    }
    LazyColumn(
        Modifier.fillMaxSize(),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        items(cards, key = { it.id }) { card ->
            TaskCardView(card) { onCancel(card.id) }
        }
    }
}

@Composable
private fun EmptyState() {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text(
                "❯_",
                style = TextStyle(brush = GradTitle, fontWeight = FontWeight.Black, fontSize = 40.sp),
            )
            Spacer(Modifier.height(8.dp))
            Text("等待指挥…", style = MaterialTheme.typography.labelLarge, color = TextDim)
            Text(
                "试试：对 sample.apk 做静态分析 / frida 插桩某App",
                style = MaterialTheme.typography.labelSmall,
                color = TextDim,
            )
        }
    }
}

@Composable
fun TaskCardView(card: TaskCard, onCancel: () -> Unit) {
    val glassColor = if (card.status == TaskStatus.RUNNING || card.status == TaskStatus.TOOL)
        SurfaceGlassHi else SurfaceGlass
    Box(
        Modifier
            .fillMaxWidth()
            .clip(MaterialTheme.shapes.medium)
            .background(glassColor)
            .border(1.dp, GradCardBorder, MaterialTheme.shapes.medium)
            .padding(14.dp)
    ) {
        Column(Modifier.fillMaxWidth()) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                StatusBadge(card.status)
                Spacer(Modifier.width(8.dp))
                Text(card.skillId, style = MaterialTheme.typography.labelMedium, color = NeonCyan)
            }
            Spacer(Modifier.height(8.dp))
            Text(card.input, style = MaterialTheme.typography.bodyMedium, color = TextHi)
            card.error?.let {
                Spacer(Modifier.height(6.dp))
                Text("✕ $it", style = MaterialTheme.typography.bodySmall, color = ErrorRed)
            }
            if (card.reasoning.isNotBlank()) {
                Spacer(Modifier.height(6.dp))
                Text(
                    "思考 ${card.reasoning}",
                    style = MaterialTheme.typography.bodySmall,
                    color = TextLo,
                )
            }
            if (card.output.isNotBlank()) {
                Spacer(Modifier.height(6.dp))
                Text(card.output.toString(), style = MaterialTheme.typography.bodyMedium, color = TextHi)
            }
            card.tools.forEach {
                Spacer(Modifier.height(4.dp))
                ToolRow(it)
            }
            if (card.cost.promptTokens > 0) {
                Spacer(Modifier.height(8.dp))
                val hit = card.cost.cacheHitRate
                val hitColor = if (hit >= 0.9f) NeonCyan else TextDim
                Text(
                    "tokens ${card.cost.promptTokens}+${card.cost.completionTokens} · 缓存 ${"%.1f".format(hit * 100)}% · ¥${"%.4f".format(card.cost.totalCostCny)}",
                    style = MaterialTheme.typography.labelSmall,
                    color = hitColor,
                )
            }
            if (!card.status.isTerminal) {
                Spacer(Modifier.height(4.dp))
                TextButton(onClick = onCancel, modifier = Modifier.align(Alignment.End)) {
                    Text("取消", color = NeonCyan)
                }
            }
        }
    }
}

@Composable
private fun StatusBadge(status: TaskStatus) {
    val label: String
    val dotColor: Color
    val pulse: Boolean
    when (status) {
        TaskStatus.PENDING -> { label = "排队中"; dotColor = TextDim; pulse = false }
        TaskStatus.RUNNING -> { label = "执行中"; dotColor = NeonPurple; pulse = true }
        TaskStatus.TOOL -> { label = "调工具"; dotColor = NeonMagenta; pulse = true }
        TaskStatus.DONE -> { label = "完成"; dotColor = NeonCyan; pulse = false }
        TaskStatus.FAILED -> { label = "失败"; dotColor = ErrorRed; pulse = false }
        TaskStatus.CANCELLED -> { label = "已取消"; dotColor = TextDim; pulse = false }
    }
    Row(verticalAlignment = Alignment.CenterVertically) {
        if (pulse) {
            val transition = rememberInfiniteTransition(label = "dot")
            val a by transition.animateFloat(
                0.3f, 1f,
                infiniteRepeatable(tween(480), RepeatMode.Reverse),
                label = "dotA",
            )
            Box(Modifier.size(8.dp).background(dotColor, CircleShape).alpha(a))
        } else {
            Box(Modifier.size(8.dp).background(dotColor, CircleShape))
        }
        Spacer(Modifier.width(6.dp))
        Text(label, style = MaterialTheme.typography.labelMedium, color = TextLo)
    }
}

@Composable
private fun ToolRow(tool: ToolCall) {
    Text(
        "❯ ${tool.name}",
        style = MaterialTheme.typography.labelSmall,
        color = PinkTint,
    )
    if (tool.arguments.isNotBlank()) {
        Text(
            tool.arguments.take(120) + if (tool.arguments.length > 120) "…" else "",
            style = MaterialTheme.typography.labelSmall,
            color = TextDim,
        )
    }
    tool.result?.let {
        Text(it.take(180), style = MaterialTheme.typography.labelSmall, color = TextLo)
    }
}

@Composable
private fun InputBar(input: String, busy: Boolean, onChange: (String) -> Unit, onRun: () -> Unit) {
    Row(
        Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        OutlinedTextField(
            value = input,
            onValueChange = onChange,
            modifier = Modifier.weight(1f),
            placeholder = { Text("❯ 向 Agent 下达任务…", color = TextDim) },
            enabled = !busy,
            singleLine = true,
            shape = MaterialTheme.shapes.small,
            colors = OutlinedTextFieldDefaults.colors(
                focusedBorderColor = NeonCyan,
                unfocusedBorderColor = NeonPurple.copy(alpha = 0.45f),
                focusedContainerColor = BgSlate,
                unfocusedContainerColor = BgSlate,
                disabledContainerColor = BgSlate,
                cursorColor = NeonCyan,
                focusedTextColor = TextHi,
                unfocusedTextColor = TextHi,
                disabledTextColor = TextLo,
            ),
        )
        val runEnabled = input.isNotBlank() && !busy
        Box(
            Modifier
                .clip(CircleShape)
                .background(
                    if (busy) Brush.linearGradient(listOf(NeonPurple.copy(alpha = 0.35f)))
                    else Brush.linearGradient(listOf(NeonPurple, NeonMagenta, NeonCyan))
                )
                .alpha(if (runEnabled) 1f else 0.55f)
                .clickable(enabled = runEnabled, onClick = onRun)
                .padding(horizontal = 18.dp, vertical = 14.dp)
        ) {
            Text(
                if (busy) "执行中…" else "▶ 执行",
                style = MaterialTheme.typography.labelLarge,
                color = if (busy) TextLo else BgInk,
            )
        }
    }
}