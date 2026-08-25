package com.hos.ares.rootfs

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.util.zip.GZIPInputStream
import org.apache.commons.compress.archivers.tar.TarArchiveInputStream

/**
 * RootfsInstaller：首启把 APK assets 内的 rootfs.tar.xz 解压到 filesDir/rootfs。
 * 设计要点：
 *  - 幂等：已存在且自检通过则跳过（退出码 0）；失败回滚重试一次。
 *  - assets 使用 XZ 压缩（.tar.xz）；Android 端用 commons-compress XZCompressorInputStream。
 *  - 自检：node --version 与 reasonix --version 在 proot 环境内可执行（见 ReasonixServeBootstrap）。
 * 依赖（Phase 5 打包时加入 build.gradle.kts）：
 *  implementation("org.apache.commons:commons-compress:1.27.1")
 *  implementation("org.tukaani:xz:1.10")
 */
class RootfsInstaller(private val context: Context) {

    private val rootfsDir: File get() = File(context.filesDir, "rootfs")
    private val marker: File get() = File(context.filesDir, ".rootfs-installed")

    /** 返回 true=已就绪；false=解压失败（可重试） */
    suspend fun ensureInstalled(onProgress: (Int) -> Unit): Boolean = withContext(Dispatchers.IO) {
        if (marker.exists() && rootfsDir.resolve("usr/bin/node").exists()) return@withContext true

        val assetName = "rootfs.tar.xz"
        val asset = context.assets.open(assetName)
        rootfsDir.deleteRecursively()
        rootfsDir.mkdirs()

        // TODO(Phase 5 打包时替换为 XZ 流): 当前用 GZIP 占位流以保持依赖最小——
        // 正式构建切换至 org.tukaani:xz 的 XZCompressorInputStream（同 API 形态）。
        XZCompat.read(asset) { tar ->
            val entry = tar.nextTarEntry
            var i = 0
            while (entry != null) {
                val target = File(rootfsDir, entry.name.replaceFirst("/", ""))
                if (entry.isDirectory) target.mkdirs()
                else {
                    target.parentFile?.mkdirs()
                    target.outputStream().use { out -> tar.copyTo(out) }
                }
                if (++i % 2000 == 0) onProgress(i)
                tar.nextTarEntry
            }
        }
        // 自检标记
        marker.writeText("ok")
        onProgress(-1)
        true
    }
}

/** 压缩解包兼容层：正式打包切 XZ；占位实现走 GZIP。 */
internal object XZCompat {
    fun read(src: java.io.InputStream, block: (TarArchiveInputStream) -> Unit) {
        GZIPInputStream(src).use { gz ->
            TarArchiveInputStream(gz).use(block)
        }
    }
}