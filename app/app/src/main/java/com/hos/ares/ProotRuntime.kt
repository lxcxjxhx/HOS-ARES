package com.hos.ares

import android.content.Context
import org.apache.commons.compress.archivers.tar.TarArchiveInputStream
import org.apache.commons.compress.compressors.gzip.GzipCompressorInputStream
import java.io.BufferedReader
import java.io.File
import java.io.FileOutputStream
import java.io.InputStreamReader

/**
 * ProotRuntime — 在 Android 上通过 proot 运行 Alpine Linux 中的 Agent（原创实现）
 *
 * 技术路径（参考 reasonix-proot-app 的 proot 思路，但代码为原创）：
 *   1. 首次启动时在 App 私有目录解包内置的 Alpine rootfs
 *   2. 用 proot 以"无 root"方式 chroot 进 rootfs，执行 Linux 安全工具
 *   3. 每个 agent 命令都在 proot 环境内、以指定 shell 脚本运行
 *
 * 这样 Argus / RepoAudit / Strix / PentestGPT / DeepAudit 等 Linux 工具
 * 都能在手机端原生运行，不再依赖电脑或 Docker。
 */
class ProotRuntime(private val context: Context) {

    /** 内置到 assets 的 agent 启动脚本清单（对应 ares-rootfs/agents/<name>/run.sh）。 */
    private val agents = listOf("argus", "repoaudit", "strix", "pentestgpt", "deepaudit")

    private val rootfsDir: File
        get() = File(context.filesDir, "alpine-rootfs")

    private val binDir: File
        get() = File(context.filesDir, "bin")

    /** rootfs 解包完成的标记文件，用于避免重复解包。 */
    private val setupMarker: File
        get() = File(rootfsDir, ".hosaressetup")

    /** 返回 proot 可执行文件路径；未打包 proot 二进制时返回 null。 */
    private fun prootBinary(): String? {
        val candidates = listOf(
            File(context.applicationInfo.nativeLibraryDir, "libproot.so"),
            File(context.filesDir, "proot"),
            File(binDir, "proot"),
        )
        return candidates.firstOrNull { it.exists() }?.absolutePath
    }

    /** 构造 proot 命令前缀（不含 agent 命令本体）。 */
    private fun prootPrefix(): String? {
        val proot = prootBinary() ?: return null
        if (!rootfsDir.exists()) return null
        return "$proot -0 -r ${rootfsDir.absolutePath} " +
            "-b /dev -b /proc -b /sys -b /sdcard:/sdcard -w /"
    }

    /**
     * 首次启动初始化：解包 Alpine rootfs、安装 proot 二进制、写入 agent 启动脚本。
     * 幂等——已完成时直接返回成功。
     */
    fun initIfNeeded(): Result<Unit> {
        return try {
            if (setupMarker.exists()) {
                return Result.Success(Unit)
            }
            // 1. 安装 proot 静态二进制到 filesDir/bin/proot
            binDir.mkdirs()
            val prootFile = File(binDir, "proot")
            if (!prootFile.exists()) {
                copyAsset("proot", prootFile)
                prootFile.setExecutable(true, false)
            }
            // 2. 解包 Alpine minirootfs 到 filesDir/alpine-rootfs
            if (!rootfsDir.exists() || !File(rootfsDir, "etc").exists()) {
                extractRootfs()
            }
            // 3. 拷贝各 agent 运行时源码到 rootfs/opt/agents
            copyAssetDir("opt/agents", File(rootfsDir, "opt/agents"))
            // 3.1 安装默认 token 优化 skills 到 rootfs/opt/skills
            installDefaultSkills()
            // 4. 写入各 agent 启动脚本到 rootfs/opt/agents/<name>/run.sh
            for (agent in agents) {
                val script = File(rootfsDir, "opt/agents/$agent/run.sh")
                if (!script.exists()) {
                    copyAsset("agents/$agent/run.sh", script)
                    script.setExecutable(true, false)
                }
            }
            // 5. 拷贝并执行引导脚本（首次安装 python3 + 依赖）
            val bootstrap = File(rootfsDir, "bootstrap.sh")
            if (!bootstrap.exists()) {
                copyAsset("bootstrap.sh", bootstrap)
                bootstrap.setExecutable(true, false)
            }
            runBootstrap(bootstrap)
            setupMarker.writeText("done")
            Result.Success(Unit)
        } catch (e: Exception) {
            Result.Failure(e.message ?: "rootfs 初始化失败")
        }
    }

    /** 通过 proot 在 rootfs 内执行引导脚本（失败仅告警，不阻断）。 */
    private fun runBootstrap(bootstrap: File) {
        val proot = prootBinary() ?: return
        val cmd = "$proot -0 -r ${rootfsDir.absolutePath} -b /dev -b /proc -b /sys -w / /bin/sh /bootstrap.sh"
        runShell(cmd)
    }

    /**
     * 在 proot Alpine 环境中执行指定 agent。
     * [env] 提供注入到 rootfs 进程的环境变量（如 LLM Key）。
     * 返回 stdout+stderr 文本。执行前确保 rootfs 已初始化。
     */
    fun runAgent(agent: String, task: String, env: Map<String, String> = emptyMap()): Result<String> {
        val init = initIfNeeded()
        if (init is Result.Failure) {
            return Result.Failure("rootfs 未就绪: ${init.error}")
        }
        val prefix = prootPrefix()
        if (prefix == null) {
            return Result.Failure("proot 运行时未就绪（rootfs 或 proot 二进制缺失）")
        }
        // 每个 agent 对应 rootfs 内 /opt/agents/<name>/run.sh 的启动脚本
        val script = "/opt/agents/$agent/run.sh"
        val cmd = "$prefix /bin/sh $script \"$task\""
        return runShell(cmd, env)
    }

    /** 把 assets 里的文件拷贝到目标路径。 */
    private fun copyAsset(assetName: String, dest: File) {
        dest.parentFile?.mkdirs()
        context.assets.open(assetName).use { input ->
            FileOutputStream(dest).use { output -> input.copyTo(output) }
        }
    }

    /** 递归拷贝 assets 下的整个目录树到目标路径。 */
    private fun copyAssetDir(assetDir: String, destDir: File) {
        destDir.mkdirs()
        for (child in context.assets.list(assetDir) ?: emptyArray()) {
            val assetChild = "$assetDir/$child"
            if (child.endsWith("/") || context.assets.list(assetChild)?.isNotEmpty() == true) {
                // 目录
                copyAssetDir(assetChild, File(destDir, child))
            } else {
                // 文件
                copyAsset(assetChild, File(destDir, child))
            }
        }
    }

    /** 将内置的 Alpine minirootfs 解包到 rootfsDir。 */
    private fun extractRootfs() {
        rootfsDir.mkdirs()
        // AGP 可能把 .tar.gz 资产自动解压为 .tar 打进 APK，需兼容两种命名。
        val gzName = "alpine-minirootfs.tar.gz"
        val rawName = "alpine-minirootfs.tar"
        val isGz = assetsContains(gzName)
        context.assets.open(if (isGz) gzName else rawName).use { input ->
            val stream = if (isGz) GzipCompressorInputStream(input) else input
            TarArchiveInputStream(stream).use { tar ->
                var entry = tar.nextTarEntry
                while (entry != null) {
                    val name = entry.name.removePrefix("./")
                    val dest = File(rootfsDir, name)
                    if (entry.isDirectory) {
                        dest.mkdirs()
                    } else {
                        dest.parentFile?.mkdirs()
                        FileOutputStream(dest).use { out -> tar.copyTo(out) }
                    }
                    entry = tar.nextTarEntry
                }
            }
        }
    }

    /** 判断某个顶层资产是否存在。 */
    private fun assetsContains(name: String): Boolean {
        return try {
            context.assets.open(name).use { true }
        } catch (e: Exception) {
            false
        }
    }

    private fun runShell(command: String, env: Map<String, String> = emptyMap()): Result<String> {
        return try {
            val builder = ProcessBuilder("/system/bin/sh", "-c", command)
            builder.environment().putAll(env)
            val proc = builder.start()
            val out = StringBuilder()
            val reader = BufferedReader(InputStreamReader(proc.inputStream))
            var line: String?
            while (reader.readLine().also { line = it } != null) {
                out.append(line).append('\n')
            }
            val code = proc.waitFor()
            if (code == 0) Result.Success(out.toString().trim())
            else Result.Failure("exit=$code: ${out.toString().trim()}")
        } catch (e: Exception) {
            Result.Failure(e.message ?: "shell error")
        }
    }

    /** 安装默认 token 优化 skills 到 rootfs/opt/skills（幂等，失败不阻断）。 */
    private fun installDefaultSkills() {
        try {
            val src = "skills"
            if (context.assets.list(src).isNullOrEmpty()) return
            copyAssetDir(src, File(rootfsDir, "opt/skills"))
        } catch (e: Exception) {
            // skills 仅为默认体验增强，安装失败不阻断初始化
        }
    }
}
