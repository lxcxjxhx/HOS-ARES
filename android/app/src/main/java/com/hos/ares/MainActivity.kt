package com.hos.ares

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.lifecycle.viewmodel.compose.viewModel
import com.hos.ares.ui.AresHomeScreen

/**
 * HOS-ARES Android 入口（Phase 4 骨架）。
 * 架构：L1 Compose UI → AresViewModel → AresGateway → reasonix serve（Termux 内 HTTP+SSE）。
 * 全部传输实现依赖 Phase 5 打包环境（Gradle/AGP）；本骨架先固定 UI↔VM↔Gateway 契约。
 */
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                val vm: AresViewModel = viewModel()
                AresHomeScreen(vm)
            }
        }
    }
}