package com.hos.ares.ui.theme

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.shape.CircleShape

// Ares-V3 Neon 暗色配色（强制暗色：渗透终端不打亮）
private val NeonDarkScheme = darkColorScheme(
    primary = NeonPurple,
    onPrimary = BgInk,
    primaryContainer = SurfaceGlass,
    onPrimaryContainer = TextHi,
    secondary = NeonMagenta,
    onSecondary = BgInk,
    secondaryContainer = SurfaceGlassHi,
    onSecondaryContainer = TextHi,
    tertiary = NeonCyan,
    onTertiary = BgInk,
    tertiaryContainer = SurfaceGlass,
    onTertiaryContainer = TextHi,
    background = BgInk,
    onBackground = TextHi,
    surface = SurfaceGlass,
    onSurface = TextHi,
    surfaceVariant = BgSlate,
    onSurfaceVariant = TextLo,
    surfaceContainer = BgSlate,
    surfaceContainerHigh = SurfaceGlassHi,
    error = ErrorRed,
    onError = BgInk,
    errorContainer = ErrorRed.copy(alpha = 0.18f),
    onErrorContainer = ErrorRed,
    outline = NeonPurple.copy(alpha = 0.55f),
    outlineVariant = TextDim,
    scrim = BgInk,
)

private val NeonShapes = Shapes(
    extraSmall = RoundedCornerShape(8.dp),
    small = RoundedCornerShape(12.dp),
    medium = RoundedCornerShape(20.dp),
    large = RoundedCornerShape(28.dp),
    extraLarge = RoundedCornerShape(32.dp),
)

@Composable
fun HosAresTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = NeonDarkScheme,
        typography = HosAresTypography,
        shapes = NeonShapes,
        content = content,
    )
}

// 供徽章/状态点复用的形状
object AresShapes {
    val Pill = RoundedCornerShape(percent = 50)
    val Dot = CircleShape
}