package com.hos.ares

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.util.Log
import org.apache.commons.compress.archivers.tar.TarArchiveInputStream
import org.apache.commons.compress.compressors.gzip.GzipCompressorInputStream
import java.io.BufferedReader
import java.io.File
import java.io.FileOutputStream
import java.io.InputStreamReader
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.coroutines.resume
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

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
/**
 * 运行时就绪状态，供 UI 展示与执行门控。
 */
enum class RuntimeState {
    /** 正在初始化（解包 / 装依赖 / 自愈）。 */
    INITIALIZING,
    /** 已就绪，可运行 agent。 */
    READY,
    /** 基础已就绪但依赖未装完（通常首次需联网）。 */
    NEEDS_NETWORK,
    /** 初始化失败。 */
    ERROR,
}

class ProotRuntime(private val context: Context) {

    companion object {
        /** 内置 agent 资产版本。升级时若新增/更新了 agents 资产（源码、run.sh、llm_connect 等），
         *  递增此值，保证旧 rootfs 也能自动补拷，无需用户清除数据。 */
        private const val ASSETS_VERSION = 1
    }

    /** 运行时就绪状态（供 UI 观察）。 */
    private val _state = MutableStateFlow(RuntimeState.INITIALIZING)
    val state: StateFlow<RuntimeState> = _state

    /** 初始化互斥锁：防止启动流程与执行流程并发重入损坏 rootfs。 */
    private val initMutex = Mutex()

    /** 最近一次初始化失败的原因。 */
    private var lastError: String = ""

    /** 内置到 assets 的 agent 启动脚本清单（对应 ares-rootfs/agents/<name>/run.sh）。 */
    private val agents = listOf("argus", "repoaudit", "strix", "pentestgpt", "deepaudit", "securityresearch", "reasonix")

    /** 手机端默认 rootfs 路径（用户指定，与 TerminalActivity 统一）。 */
    private val rootfsDir: File
        get() = File("/sdcard/data/.Ares")

    /** proot 二进制等可执行组件仍放 App 私有目录：/sdcard 为 noexec 挂载，不可直接执行。 */
    private val binDir: File
        get() = File(context.filesDir, "bin")

    /** rootfs 解包完成的标记文件，用于避免重复解包。 */
    private val setupMarker: File
        get() = File(rootfsDir, ".hosaressetup")

    /** bootstrap（依赖安装）完成的标记文件，仅当成功且检测到 python3 才写入。 */
    private val bootstrapMarker: File
        get() = File(rootfsDir, ".hosaressetup-bootstrapped")

    /** agent 资产版本标记文件，用于升级时自动补拷 assets 里的 agent 源码/脚本。 */
    private val assetVersionMarker: File
        get() = File(rootfsDir, ".hosaressets-version")

    /** 返回 proot 可执行文件路径；未打包 proot 二进制时返回 null。 */
    private fun prootBinary(): String? {
        val candidates = listOf(
            File(context.applicationInfo.nativeLibraryDir, "proot.so"),
            File(context.applicationInfo.nativeLibraryDir, "libproot.so"),
            File(context.filesDir, "proot"),
            File(binDir, "proot"),
        )
        return candidates.firstOrNull { it.exists() }?.absolutePath
    }

    /**
     * proot 需要的宿主环境变量：PROOT_TMP_DIR 指向 App 私有可写目录。
     * Android 上宿主默认 /tmp 不存在/不可写，proot 创建 glue rootfs 临时目录时会报
     * "can't create temporary directory"，因此必须显式指向可写位置（cacheDir 保证可写）。
     * 所有启动 proot 的进程都应注入该环境变量。
     */
    private fun prootEnv(): Map<String, String> {
        val tmp = File(context.cacheDir, "proot-tmp").apply { mkdirs() }
        val env = HashMap<String, String>()
        env["PROOT_TMP_DIR"] = tmp.absolutePath
        // /sdcard 挂载为 noexec：proot 必须通过 PROOT_LOADER 才能执行 rootfs 内的 ELF
        // （busybox/sh/python），否则启动即 Permission denied。
        val loader = File(context.applicationInfo.nativeLibraryDir, "loader.so")
        if (loader.exists()) env["PROOT_LOADER"] = loader.absolutePath
        return env
    }

    /**
     * 构造 proot 命令前缀（不含 agent 命令本体）。
     * [projectDir] 非空时作为隔离工作目录 bind 到 /work 并以其为工作目录；
     * 为空时仅以 / 为工作目录，不做额外绑定。
     */
    private fun prootPrefix(projectDir: String?): String? {
        val proot = prootBinary() ?: return null
        if (!rootfsDir.exists()) return null
        val prefix = "$proot -0 -r ${rootfsDir.absolutePath} " +
            "-b /dev -b /proc -b /sys --kill-on-exit --link2symlink --sysvipc"
        return if (projectDir != null) {
            "$prefix -b $projectDir:/work -w /work"
        } else {
            "$prefix -w /"
        }
    }

    /**
     * 后台自愈式初始化：校验 proot 二进制、rootfs、agent 脚本，并确保依赖已装好。
     * 幂等、可重入（互斥保护），返回就绪状态。
     */
    suspend fun init(): RuntimeState = initMutex.withLock {
        withContext(Dispatchers.IO) {
            _state.value = RuntimeState.INITIALIZING
            val ready = try {
                ensureExtracted()
                // 依赖未就绪且当前无网络时，跳过 bootstrap，置为 NEEDS_NETWORK（而非失败）
                if (!isPythonReady() && !hasNetwork()) {
                    _state.value = RuntimeState.NEEDS_NETWORK
                    return@withContext RuntimeState.NEEDS_NETWORK
                }
                ensureBootstrapped()
                // 汇总最终就绪：基础组件齐全且 python3 可用
                if (bootstrapMarker.exists() || isPythonReady()) {
                    prootBinary() != null &&
                        rootfsDir.exists() &&
                        File(rootfsDir, "etc").exists()
                } else {
                    false
                }
            } catch (e: Exception) {
                lastError = e.message ?: "初始化失败"
                _state.value = RuntimeState.ERROR
                return@withContext RuntimeState.ERROR
            }
            _state.value = if (ready) RuntimeState.READY else RuntimeState.NEEDS_NETWORK
            _state.value
        }
    }

    /** 最近一次初始化失败原因（供 UI/日志展示）。 */
    fun lastInitError(): String = lastError

    /**
     * 主机网络连通性探活：通过 ConnectivityManager 判断当前是否存在可用的 INTERNET 网络。
     * 无活动网络或网络不具备 INTERNET 能力时返回 false；无法判定时按可用处理，避免误判离线。
     */
    private fun hasNetwork(): Boolean {
        return try {
            val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager ?: return true
            val network = cm.activeNetwork ?: return false
            val caps = cm.getNetworkCapabilities(network) ?: return false
            caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
        } catch (e: Exception) {
            true
        }
    }

    /**
     * 自愈式解包基础组件（幂等）：
     * 1. proot 二进制缺失则重新拷贝并置可执行；
     * 2. rootfs 缺失/损坏（无 etc）则重新解包；
     * 3. 一次性：拷贝默认 skills、bootstrap.sh（setupMarker 存在则跳过）；
     * 4. agent 资产版本化：本地资产版本落后于内置 ASSETS_VERSION 时，强制补拷
     *    agents 源码 / run.sh / llm_connect / requirements，保证升级后新工具自动可用，
     *    无需用户清除数据。
     */
    private fun ensureExtracted() {
        // 1. proot 二进制：缺失自愈
        binDir.mkdirs()
        val prootFile = File(binDir, "proot")
        if (prootBinary() == null) {
            copyAsset("proot", prootFile)
            prootFile.setExecutable(true, false)
        }
        // 2. rootfs：缺失/损坏自愈（校验 etc + bin/sh，防止 /sdcard 解压半途失败残留）
        if (!rootfsDir.exists() || !File(rootfsDir, "etc").exists() || !File(rootfsDir, "bin/sh").exists()) {
            extractRootfs()
        }
        // 3. 基础骨架一次性拷贝（setupMarker 存在则跳过）
        if (!setupMarker.exists()) {
            installDefaultSkills()
            val bootstrap = File(rootfsDir, "bootstrap.sh")
            if (!bootstrap.exists()) {
                copyAsset("bootstrap.sh", bootstrap)
                bootstrap.setExecutable(true, false)
            }
            setupMarker.writeText("done")
        }
        // 4. agent 资产版本化：版本落后则强制补拷（含源码、run.sh、llm_connect、requirements）
        if (needsAssetRefresh()) {
            copyAssetDir("opt/agents", File(rootfsDir, "opt/agents"))
            copyAssetDir("requirements", File(rootfsDir, "opt/agents-requirements"))
            for (agent in agents) {
                val script = File(rootfsDir, "opt/agents/$agent/run.sh")
                copyAsset("agents/$agent/run.sh", script)
                script.setExecutable(true, false)
            }
            assetVersionMarker.writeText(ASSETS_VERSION.toString())
        }
    }

    /** 本地 agent 资产版本是否落后于内置版本（标记缺失视为 0）。 */
    private fun needsAssetRefresh(): Boolean {
        val cur = try {
            assetVersionMarker.readText().trim().toInt()
        } catch (e: Exception) {
            0
        }
        return cur < ASSETS_VERSION
    }

    /**
     * 确保依赖已安装：若未 bootstrap 完成则重试运行引导脚本。
     * 仅当引导成功且检测到 python3 时才写完成标记；失败不写，下次自动重试。
     */
    private fun ensureBootstrapped() {
        if (isPythonReady()) {
            if (!bootstrapMarker.exists()) bootstrapMarker.writeText("done")
            return
        }
        if (bootstrapMarker.exists()) return // 已标记完成但 python 缺失 → 交由后续自愈
        val bootstrap = File(rootfsDir, "bootstrap.sh")
        if (!bootstrap.exists()) return
        if (runBootstrap(bootstrap) && isPythonReady()) {
            bootstrapMarker.writeText("done")
        }
    }

    /** 检测 rootfs 内 python3 是否可用（作为依赖就绪的判定）。 */
    private fun isPythonReady(): Boolean {
        val proot = prootBinary() ?: return false
        if (!rootfsDir.exists()) return false
        val cmd = "$proot -0 -r ${rootfsDir.absolutePath} -b /dev -b /proc -b /sys -w / " +
            "/bin/sh -c 'command -v python3'"
        return runShell(cmd, prootEnv()) is Result.Success
    }

    /** 通过 proot 在 rootfs 内执行引导脚本；成功返回 true，失败仅告警不阻断。 */
    private fun runBootstrap(bootstrap: File): Boolean {
        val proot = prootBinary() ?: return false
        val cmd = "$proot -0 -r ${rootfsDir.absolutePath} -b /dev -b /proc -b /sys -w / /bin/sh /bootstrap.sh"
        return runShell(cmd, prootEnv()) is Result.Success
    }

    /**
     * 在 proot Alpine 环境中执行指定 agent（沙盒隔离 + 超时 + 取消 + 行级流式输出）。
     * [projectDir] 非空时作为隔离工作目录 bind 到 /work 并以其为工作目录；
     * [timeoutMillis] >0 时超时强制结束进程并返回超时失败；
     * [onOutput] 每收到一行文本回调一次（含换行）。
     */
    suspend fun runAgent(
        agent: String,
        task: String,
        projectDir: String?,
        env: Map<String, String>,
        timeoutMillis: Long,
        onOutput: (String) -> Unit,
    ): Result<String> = withContext(Dispatchers.IO) {
        // 运行前先做一次带自愈的初始化并检查就绪状态
        val st = init()
        if (st == RuntimeState.ERROR) {
            return@withContext Result.Failure("rootfs 未就绪: ${lastError.ifEmpty { "初始化失败" }}")
        }
        if (st != RuntimeState.READY) {
            return@withContext Result.Failure(
                "运行环境未就绪（首次使用需联网完成依赖安装，请联网后重试）"
            )
        }
        val prefix = prootPrefix(projectDir)
        if (prefix == null) {
            return@withContext Result.Failure("proot 运行时未就绪（rootfs 或 proot 二进制缺失）")
        }
        // reasonix 统一入口：rootfs 无 /opt/agents/*/run.sh（构建产物缺省），
        // 直接调 reasonix run 无人值守模式，并从 env 读取自循环/YOLO 开关拼参数。
        // task 经 HOS_TASK 环境变量传入，绕开外层 /system/bin/sh 的引号/命令替换解析。
        val cmd = if (agent == "reasonix") {
            val loop = if (env["HOS_SELF_LOOP"] == "1") "--max-steps 0 " else ""
            val yolo = if (env["HOS_YOLO"] == "1") "--yolo" else "--auto"
            "$prefix /bin/sh -c 'export HOME=/root; export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; " +
                "exec reasonix run $loop$yolo \"\$(printf %s \"\$HOS_TASK\")\"' sh"
        } else {
            "$prefix /bin/sh /opt/agents/$agent/run.sh /work \"$task\""
        }

        val builder = ProcessBuilder("/system/bin/sh", "-c", cmd)
        builder.redirectErrorStream(true) // 合并 stdout+stderr，避免读管道死锁
        builder.environment().putAll(env)
        if (agent == "reasonix") builder.environment()["HOS_TASK"] = task
        builder.environment().putAll(prootEnv()) // PROOT_TMP_DIR → App 私有可写目录，避免 Android 上 /tmp 不可写
        val proc = builder.start()

        suspendCancellableCoroutine<Result<String>> { cont ->
            // 协程取消时强制结束进程
            cont.invokeOnCancellation { proc.destroyForcibly() }

            val timedOut = AtomicBoolean(false)
            // 超时 watchdog：超时后强制结束进程并标记本次执行为超时
            var watchdog: Thread? = null
            if (timeoutMillis > 0) {
                watchdog = Thread {
                    try {
                        Thread.sleep(timeoutMillis)
                        timedOut.set(true)
                        proc.destroyForcibly()
                    } catch (e: InterruptedException) {
                        // 进程已结束，watchdog 被中断退出
                    }
                }.apply { isDaemon = true; start() }
            }

            // 后台线程逐行读取合并输出，实时回调并累积全文
            val readerThread = Thread {
                val full = StringBuilder()
                try {
                    val reader = BufferedReader(InputStreamReader(proc.inputStream))
                    var line: String?
                    while (reader.readLine().also { line = it } != null) {
                        val text = line + "\n"
                        full.append(text)
                        onOutput(text)
                    }
                } catch (e: Exception) {
                    // 进程被销毁/取消，输入流被关闭
                }
                val code = try { proc.waitFor() } catch (e: InterruptedException) { -1 }
                watchdog?.interrupt()
                if (!cont.isActive) return@Thread // 已被取消，由取消路径处理
                if (timedOut.get()) {
                    cont.resume(Result.Failure("超时: $agent 运行超过 ${timeoutMillis}ms"))
                } else if (code == 0) {
                    cont.resume(Result.Success(full.toString().trim()))
                } else {
                    cont.resume(Result.Failure("exit=$code: ${full.toString().takeLast(2000)}"))
                }
            }
            readerThread.isDaemon = true
            readerThread.start()
        }
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

    /**
     * 将内置 rootfs 解包到 rootfsDir。优先使用完整预装环境 rootfs.tar（与
     * TerminalActivity 一致，含 python3/依赖/工具），缺失时才回退 minirootfs。
     * 正确处理 symlink：minirootfs 中 /bin/sh 等是指向 busybox 的软链，
     * 若当普通文件写出会变成 0 字节文件，导致 '/bin/sh' not found。
     */
    private fun extractRootfs() {
        rootfsDir.mkdirs()
        // 完整预装 rootfs（gzip 压缩 tar）；rootfs.tar 由构建流程生成，始终为 gzip。
        val fullName = "rootfs.tar"
        // AGP 可能把 .tar.gz 资产自动解压为 .tar 打进 APK，需兼容两种命名。
        val gzName = "alpine-minirootfs.tar.gz"
        val rawName = "alpine-minirootfs.tar"
        val useFull = assetsContains(fullName)
        val isGz = assetsContains(gzName)
        val assetName = when {
            useFull -> fullName
            isGz -> gzName
            else -> rawName
        }
        context.assets.open(assetName).use { input ->
            // rootfs.tar 与 alpine-minirootfs.tar.gz 均为 gzip；.tar 裸格式不压缩。
            val stream = if (assetName.endsWith(".gz") || assetName == fullName)
                GzipCompressorInputStream(input) else input
            TarArchiveInputStream(stream).use { tar ->
                var entry = tar.nextTarEntry
                while (entry != null) {
                    val name = entry.name.removePrefix("./")
                    val dest = File(rootfsDir, name)
                    when {
                        entry.isSymbolicLink -> {
                            dest.parentFile?.mkdirs()
                            createSymlink(entry.linkName, dest)
                        }
                        entry.isDirectory -> dest.mkdirs()
                        entry.isFile -> {
                            dest.parentFile?.mkdirs()
                            FileOutputStream(dest).use { out -> tar.copyTo(out) }
                        }
                        // hardlink / 其他类型：忽略（minirootfs 无关键 hardlink）
                    }
                    entry = tar.nextTarEntry
                }
            }
        }
    }

    /** 创建符号链接：优先 toybox ln（/sdcard 上 Java NIO 受限），失败则尝试 NIO 兜底。 */
    private fun createSymlink(linkName: String, dest: File) {
        var ok = false
        try {
            val p = ProcessBuilder("/system/bin/ln", "-s", linkName, dest.absolutePath)
                .redirectErrorStream(true).start()
            p.waitFor()
            ok = java.nio.file.Files.isSymbolicLink(dest.toPath())
        } catch (_: Exception) {
            // 走 NIO 兜底
        }
        if (!ok) {
            try {
                java.nio.file.Files.createSymbolicLink(
                    dest.toPath(), java.nio.file.Paths.get(linkName)
                )
                ok = java.nio.file.Files.isSymbolicLink(dest.toPath())
            } catch (e: Exception) {
                Log.w("HOSARES", "创建 symlink 失败: $dest -> $linkName (${e.message})")
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
