package com.hos.ares.ui.theme

import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color

// ─────────────────────────────────────────────────────────────
// HOS-ARES · Ares-V3 Neon 设计令牌（见 方案/…/13-Phase4-炫酷UI与图标设计.md §13.2）
// 零外部依赖：Compose 原生 Brush / Color。
// ─────────────────────────────────────────────────────────────

// 基色
val BgInk = Color(0xFF0B0710)          // 深紫黑（全屏底 / 状态栏 / 导航栏）
val BgSlate = Color(0xFF141020)        // 次级面板底
val SurfaceGlass = Color(0xFF1D1633)   // 玻璃卡片
val SurfaceGlassHi = Color(0xFF2A2150) // 玻璃卡片（悬停/聚焦态）

// 霓虹三色
val NeonPurple = Color(0xFFA855F7)     // primary · 电光紫
val NeonMagenta = Color(0xFFD946EF)    // secondary · 霓虹粉紫
val NeonCyan = Color(0xFF22D3EE)       // tertiary · 电光青（缓存/扫描线/成功）
val PinkTint = Color(0xFFF472B6)       // 工具前缀 ❯

// 语义
val ErrorRed = Color(0xFFF87171)
val WarnAmber = Color(0xFFFBBF24)

// 文本
val TextHi = Color(0xFFF5EFFF)
val TextLo = Color(0xFF9E93B8)
val TextDim = Color(0xFF6B6284)

// ── 渐变 Brush（标题 / 主按钮 / 状态光晕）────────────────────────
val GradTitle: Brush
    get() = Brush.linearGradient(listOf(NeonPurple, NeonMagenta))

val GradBrand: Brush
    get() = Brush.linearGradient(
        listOf(NeonPurple, NeonMagenta, NeonCyan),
        start = androidx.compose.ui.geometry.Offset.Zero,
        end = androidx.compose.ui.geometry.Offset.Infinite,
    )

val GradCardBorder: Brush
    get() = Brush.linearGradient(
        listOf(NeonPurple.copy(alpha = 0.85f), NeonCyan.copy(alpha = 0.55f)),
    )

val GradCyanGlow: Brush
    get() = Brush.verticalGradient(
        listOf(NeonCyan.copy(alpha = 0.35f), Color.Transparent),
    )