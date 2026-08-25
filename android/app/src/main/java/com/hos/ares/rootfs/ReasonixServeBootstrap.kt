package com.hos.ares.rootfs

import android.content.Context
import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/**
 * ReasonixServeBootstrap：在 proot rootfs 内拉起 reasonix serve（应用进程内，无 root 依赖），
 * 并做健康检查，供 AresGateway 确认主通道就绪。
 *
 * 命令形态（自包含装载，不含系统 Termux）：
 *   proot -0 -r <filesDir>/rootfs /bin/sh -c "cd /root/hos-ares && reasonix serve --addr 127.0.0.1:8931 --auth token --token <T>"
 *
 * proot 为静态二进制（assets/proot_arm64），非系统 Termux 组件。
 */
class ReasonixServeBootstrap(private val context: Context) {

    companion object {
        private const val TAG = "AresServe"
        const val ADDR = "127.0.0.1:8931"
        const val TOKEN = "hos-ares-gw-token" // 运行时最好由用户设置页覆盖
        private const val READY_DELAY_MS = 800L
        private const val MAX_PROBE = 30
    }

    private var process: Process? = null

    /** @return true=serve 已就绪（健康检查通过） */
    suspend fun start(): Boolean = withContext(Dispatchers.IO) {
        val rootfs = File(context.filesDir, "rootfs")
        val prootBin = File(context.filesDir, "bin/proot")
        if (!rootfs.resolve("usr/bin/node").exists() || !prootBin.exists()) return@withContext false

        val cmd = listOf(
            prootBin.absolutePath, "-0", "-r", rootfs.absolutePath,
            "/bin/sh", "-c",
            "cd /root/hos-ares && reasonix serve --addr $ADDR --auth token --token $TOKEN",
        )
        val pb = ProcessBuilder(cmd)
            .redirectErrorStream(true)
            .directory(context.filesDir)
        val p = pb.start()
        process = p

        for (i in 1..MAX_PROBE) {
            delay(READY_DELAY_MS)
            if (healthCheckOk()) { Log.i(TAG, "serve ready after ${i * READY_DELAY_MS}ms"); return@withContext true }
            if (!p.isAlive) { Log.e(TAG, "serve exited: ${p.inputStream.bufferedReader().readText().take(500)}"); return@withContext false }
        }
        Log.e(TAG, "serve not ready within ${MAX_PROBE * READY_DELAY_MS}ms")
        false
    }

    /** 健康检查：GET /?token= → 200（与 10 文实测协议一致，Cookie 握手在 AresGateway 侧完成） */
    private fun healthCheckOk(): Boolean = try {
        val conn = URL("http://$ADDR/?token=$TOKEN").openConnection() as HttpURLConnection
        conn.connectTimeout = 1500
        conn.readTimeout = 1500
        val ok = conn.responseCode == 200
        conn.disconnect()
        ok
    } catch (_: Exception) {
        false
    }

    fun stop() {
        process?.destroy()
        process = null
    }
}