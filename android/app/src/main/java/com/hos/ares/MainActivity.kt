package com.hos.ares

import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.viewmodel.compose.viewModel
import com.hos.ares.rootfs.ReasonixServeBootstrap
import com.hos.ares.rootfs.RootfsInstaller
import com.hos.ares.ui.AresHomeScreen
import com.hos.ares.ui.theme.HosAresTheme
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * HOS-ARES · Ares-V3 Neon 入口（Phase 4 炫酷版）。
 * enableEdgeToEdge + 沉浸深色：状态栏/导航栏随 BgInk 全沉浸，无白闪。
 * 架构：L1 Compose UI → AresViewModel → AresGateway → reasonix serve（127.0.0.1:8931 HTTP+SSE）。
 * Phase 5 首启装载：RootfsInstaller 解压 assets/rootfs.tar.xz + 注入 proot →
 * ReasonixServeBootstrap 拉起 serve（后台协程，不阻塞 UI）。
 */
class MainActivity : ComponentActivity() {

    companion object {
        private const val TAG = "AresBoot"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            HosAresTheme {
                val vm: AresViewModel = viewModel()
                AresHomeScreen(vm)
            }
        }
        bootRootfs()
    }

    /** 首启装载：解压 rootfs（幂等）→ 拉起 reasonix serve；失败仅记录，不影响 UI 可用 */
    private fun bootRootfs() {
        lifecycleScope.launch {
            val installed = withContext(Dispatchers.IO) {
                RootfsInstaller(this@MainActivity).ensureInstalled { }
            }
            Log.i(TAG, "rootfs installed=$installed")
            if (installed) {
                val ready = ReasonixServeBootstrap(this@MainActivity).start()
                Log.i(TAG, "reasonix serve ready=$ready")
            }
        }
    }
}