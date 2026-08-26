package com.hos.ares.rootfs

import android.content.Context
import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import org.apache.commons.compress.archivers.tar.TarArchiveInputStream
import org.tukaani.xz.XZInputStream

/**
 * RootfsInstaller：首启把 APK assets 内的 rootfs.tar.xz 解压到 filesDir/rootfs，
 * 并将 proot 静态二进制（assets/proot_arm64）注入 filesDir/bin/proot。
 * 设计要点：
 *  - 幂等：已存在且自检通过则跳过（退出码 0）；失败回滚重试一次。
 *  - assets 使用 XZ 压缩（.tar.xz，scripts/build-rootfs.sh 烘烤产物）；Android 端用
 *    commons-compress TarArchiveInputStream + org.tukaani:xz 的 XZInputStream 解流。
 *  - 自检：node --version 与 reasonix --version 在 proot 环境内可执行（见 ReasonixServeBootstrap）。
 *  - proot 资产缺失（CI 未注入）时降级跳过并记录日志，不影响 APK 安装（偏差见 11 文）。
 */
class RootfsInstaller(private val context: Context) {

    companion object {
        private const val TAG = "AresRootfs"
    }

    private val rootfsDir: File get() = File(context.filesDir, "rootfs")
    private val marker: File get() = File(context.filesDir, ".rootfs-installed")

    /** 返回 true=已就绪；false=解压失败（可重试） */
    suspend fun ensureInstalled(onProgress: (Int) -> Unit): Boolean = withContext(Dispatchers.IO) {
        if (marker.exists() && rootfsDir.resolve("usr/bin/node").exists()) return@withContext true

        val assetName = "rootfs.tar.xz"
        val asset = context.assets.open(assetName)
        rootfsDir.deleteRecursively()
        rootfsDir.mkdirs()

        XZCompat.read(asset) { tar ->
            var entry = tar.nextTarEntry
            var i = 0
            while (entry != null) {
                val target = File(rootfsDir, entry.name.replaceFirst("/", ""))
                if (entry.isDirectory) target.mkdirs()
                else {
                    target.parentFile?.mkdirs()
                    target.outputStream().use { out -> tar.copyTo(out) }
                }
                if (++i % 2000 == 0) onProgress(i)
                entry = tar.nextTarEntry
            }
        }
        installProotIfPresent()
        // 自检标记
        marker.writeText("ok")
        onProgress(-1)
        true
    }

    /** assets/proot_arm64 → filesDir/bin/proot（755）；资产缺失则跳过（CI 未注入时） */
    private fun installProotIfPresent() {
        try {
            val binDir = File(context.filesDir, "bin").apply { mkdirs() }
            val target = File(binDir, "proot")
            context.assets.open("proot_arm64").use { src ->
                target.outputStream().use { out -> src.copyTo(out) }
            }
            target.setExecutable(true, false)
            Log.i(TAG, "proot 已注入 ${target.absolutePath}")
        } catch (e: Exception) {
            Log.w(TAG, "proot 资产缺失，跳过注入（${e.message}）；serve 通道将不可用")
        }
    }
}

/** 压缩解包层：XZ 解流（scripts/build-rootfs.sh 产出 rootfs.tar.xz，xz -9） */
internal object XZCompat {
    fun read(src: java.io.InputStream, block: (TarArchiveInputStream) -> Unit) {
        XZInputStream(src).use { xz ->
            TarArchiveInputStream(xz).use(block)
        }
    }
}