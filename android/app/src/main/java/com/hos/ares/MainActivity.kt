package com.hos.ares

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.lifecycle.viewmodel.compose.viewModel
import com.hos.ares.ui.AresHomeScreen
import com.hos.ares.ui.theme.HosAresTheme

/**
 * HOS-ARES · Ares-V3 Neon 入口（Phase 4 炫酷版）。
 * enableEdgeToEdge + 沉浸深色：状态栏/导航栏随 BgInk 全沉浸，无白闪。
 * 架构：L1 Compose UI → AresViewModel → AresGateway → reasonix serve（127.0.0.1:8931 HTTP+SSE）。
 */
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            HosAresTheme {
                val vm: AresViewModel = viewModel()
                AresHomeScreen(vm)
            }
        }
    }
}