package com.hos.ares

import android.app.Dialog
import android.content.Intent
import android.graphics.Color
import android.graphics.drawable.ColorDrawable
import android.net.ConnectivityManager
import android.net.Network
import android.net.Uri
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.webkit.WebView
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.isVisible
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import com.hos.ares.databinding.ActivityMainBinding
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import java.io.File
import java.io.FileOutputStream

/**
 * HOS 主界面（AI IDE 聊天工作台 · 原创实现）。
 *
 * 交互范式参考 Claude Code：上方消息流（用户/AI/工具状态/HTML 产物），
 * 下方命令栏（文字 + 附件 + 发送），AI 输出流式上屏；
 * 产出 HTML 方案时气泡内提供「在应用内打开」→ WebView 全屏预览。
 *
 * 能力分级：仅展示真实预装工具作为快捷任务模板，工具调度由 AI 自行决定；
 * reasonix 未配置 LLM Key 时发送入口引导去设置。
 */
class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var proot: ProotRuntime
    private lateinit var settings: SettingsStore
    private lateinit var taskStore: TaskStore
    private lateinit var envStore: EnvironmentStore
    private lateinit var adapter: ChatAdapter
    private lateinit var taskAdapter: TaskAdapter

    // 顶栏状态：运行时就绪阶段
    private var runtimeState: RuntimeState = RuntimeState.INITIALIZING
    private var stateCollectStarted = false
    private var running = false
    private var runJob: Job? = null

    // 本次待发送附件（已复制到工作目录）
    private val pendingAttachments = mutableListOf<ChatAttachment>()

    private val isRunning: Boolean
        get() = running

    // 网络监听：联网后若运行环境未就绪则自动重试初始化/依赖安装
    private val networkCallback = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: Network) {
            lifecycleScope.launch {
                if (runtimeState != RuntimeState.READY) {
                    binding.tvStatus.text = getString(R.string.status_net_recovered)
                    proot.init()
                }
            }
        }
    }

    // SAF 文件/图片选择：复制到工作目录后作为附件
    private val pickFileLauncher =
        registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
            if (uri != null) addAttachment(uri, isImage = false)
        }
    private val pickImageLauncher =
        registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
            if (uri != null) addAttachment(uri, isImage = true)
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        proot = ProotRuntime(applicationContext)
        settings = SettingsStore(this)
        taskStore = TaskStore(this)
        envStore = EnvironmentStore(this)

        // 注册网络监听：联网后自动重试初始化/依赖安装
        try {
            getSystemService(ConnectivityManager::class.java)
                .registerDefaultNetworkCallback(networkCallback)
        } catch (_: Exception) {
            // 网络监听注册失败不影响主流程
        }

        setupChat()
        setupToolRow()
        setupActions()
        refreshTasks()

        // rootfs 部署到 /sdcard/data/.Ares 需要存储权限：已授权才初始化，否则引导授权
        if (TerminalActivity.needsStoragePermission(this)) {
            binding.tvStatus.text = "需要「所有文件访问」权限部署 rootfs，请授权"
            TerminalActivity.requestStoragePermission(this)
        } else {
            initRuntime()
        }
    }

    /** 首次启动在后台自愈式初始化（解包 rootfs / 装依赖 / 自愈），不阻塞主线程 */
    private fun initRuntime() {
        lifecycleScope.launch { proot.init() }
        if (stateCollectStarted) return
        stateCollectStarted = true
        lifecycleScope.launch {
            proot.state.collect { state ->
                runtimeState = state
                updateStatus()
            }
        }
    }

    /** API 24-29 运行时权限回调：授权成功后自动初始化。 */
    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        val writeGranted = permissions.indices.any { i ->
            i < grantResults.size &&
                permissions[i] == android.Manifest.permission.WRITE_EXTERNAL_STORAGE &&
                grantResults[i] == android.content.pm.PackageManager.PERMISSION_GRANTED
        }
        if (requestCode == 1001 && writeGranted && runtimeState != RuntimeState.READY) {
            initRuntime()
        }
    }

    /** API 30+ 从「所有文件访问」设置页返回后，若已授权则继续初始化。 */
    override fun onResume() {
        super.onResume()
        if (runtimeState != RuntimeState.READY && !TerminalActivity.needsStoragePermission(this)) {
            initRuntime()
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        htmlPreviewDialog?.dismiss()
        try {
            getSystemService(ConnectivityManager::class.java)
                .unregisterNetworkCallback(networkCallback)
        } catch (_: Exception) {
            // 注销失败无副作用
        }
        runJob?.cancel()
    }

    /** 根据运行时就绪阶段 + 会话运行，刷新顶栏状态文案。 */
    private fun updateStatus() {
        binding.tvStatus.text = when {
            isRunning -> getString(R.string.status_running)
            runtimeState == RuntimeState.READY -> getString(R.string.status_ready)
            runtimeState == RuntimeState.NEEDS_NETWORK -> getString(R.string.status_needs_network)
            runtimeState == RuntimeState.ERROR -> getString(R.string.status_error)
            else -> getString(R.string.status_init)
        }
        binding.tvStatus.setTextColor(
            ContextCompat.getColor(
                this,
                when {
                    isRunning -> R.color.primary
                    runtimeState == RuntimeState.READY -> R.color.accent_green
                    runtimeState == RuntimeState.ERROR -> R.color.error
                    else -> R.color.text_secondary
                }
            )
        )
    }

    /* ---------- 聊天流 ---------- */

    private fun setupChat() {
        binding.rvChat.layoutManager = LinearLayoutManager(this)
        adapter = ChatAdapter { html -> showHtmlPreview(html) }
        binding.rvChat.adapter = adapter
        // 欢迎消息：说明能力边界
        adapter.add(
            ChatMessage(ChatType.AI, "你好，我是 HOS ARES —— 手机上的 AI 安全/编码工作台。\n\n" +
                "直接输入需求，例如：\n" +
                "• 审计 /sdcard/MyApp 找内存泄漏\n" +
                "• 对 https://example.com 做红队测试\n" +
                "• 帮我写一份企业安全方案 HTML 页面\n\n" +
                "支持上传文件/图片作为上下文。下方快捷行中灰置的项为当前版本未集成。")
        )
    }

    private fun scrollToBottom() {
        binding.rvChat.post {
            (binding.rvChat.layoutManager as LinearLayoutManager).scrollToPosition(adapter.itemCount - 1)
        }
    }

    /** 发送：组装任务文本（附件路径 + 用户输入），跑 reasonix 统一入口。 */
    private fun sendMessage() {
        if (running) {
            Toast.makeText(this, "上一任务仍在运行，请等待完成或稍后再试", Toast.LENGTH_SHORT).show()
            return
        }
        val text = binding.etInput.text.toString().trim()
        if (text.isEmpty() && pendingAttachments.isEmpty()) {
            Toast.makeText(this, "请输入需求，例如：帮我审计这个项目", Toast.LENGTH_SHORT).show()
            return
        }
        if (!llmKeyConfigured()) {
            Toast.makeText(this, "未配置 LLM Key：请先到设置中配置 DeepSeek Key", Toast.LENGTH_LONG).show()
            startActivity(Intent(this, SettingsActivity::class.java))
            return
        }
        if (runtimeState != RuntimeState.READY) {
            Toast.makeText(this, "运行环境未就绪，请稍候或联网后重试", Toast.LENGTH_SHORT).show()
            return
        }

        val dir = envStore.current()?.directory
            ?.takeIf { it.isNotBlank() }
            ?: settings.defaultWorkspaceDir("default")
        taskStore.getOrCreate(dir, text.ifEmpty { pendingAttachments.first().name })

        // 用户气泡（含附件）
        val attach = pendingAttachments.firstOrNull()
        adapter.add(ChatMessage(ChatType.USER, text, attachment = attach))

        // 任务文本：附件路径供 reasonix 读取
        val taskText = buildString {
            pendingAttachments.forEach { append("[附件: ${it.path}]\n") }
            append(text)
        }

        // 状态行 + AI 占位
        adapter.add(ChatMessage(ChatType.STATUS, "调度 reasonix 统一入口 …"))
        var aiMsg = ChatMessage(ChatType.AI, "")
        adapter.add(aiMsg)
        scrollToBottom()

        running = true
        updateStatus()
        pendingAttachments.clear()
        renderAttachments()
        binding.etInput.setText("")

        val gw = AresGateway(proot) { settings.envMap() }
        runJob = lifecycleScope.launch {
            val outCollect = launch {
                gw.output.collect { full ->
                    // 流式：全文替换当前 AI 气泡（续传新实例保持定位有效）
                    aiMsg = adapter.replace(aiMsg, full)
                    scrollToBottom()
                }
            }
            try {
                val r = gw.run(taskText, dir, 15 * 60 * 1000L)
                val final = (r as? Result.Success)?.value ?: (r as? Result.Failure)?.error.orEmpty()
                aiMsg = handleResult(aiMsg, final, r is Result.Success)
            } catch (e: CancellationException) {
                if (!isDestroyed && !isFinishing) adapter.replace(aiMsg, "⏹ 已取消")
            } catch (e: Exception) {
                if (!isDestroyed && !isFinishing) adapter.replace(aiMsg, "✗ 出错：${e.message}")
            } finally {
                outCollect.cancel()
                running = false
                if (!isDestroyed && !isFinishing) updateStatus()
            }
        }
    }

    /** 结果落地：写入 AI 气泡；若输出含 HTML 方案，转为 HTML 卡片气泡。返回替换后的消息。 */
    private fun handleResult(aiMsg: ChatMessage, finalText: String, success: Boolean): ChatMessage {
        if (!success) {
            return adapter.replace(aiMsg, "✗ 执行失败\n$finalText")
        }
        val html = extractHtml(finalText)
        if (html != null) {
            return adapter.replace(aiMsg, "已完成 ✅ 方案已生成（HTML，${html.length} 字符），点下方按钮在应用内打开。", ChatType.HTML, html)
        }
        val updated = adapter.replace(aiMsg, finalText)
        scrollToBottom()
        return updated
    }

    /** 从 AI 输出中提取 HTML 方案（```html 代码块优先，其次 <html…</html>）。 */
    private fun extractHtml(text: String): String? {
        val fence = Regex("```html\\s*([\\s\\S]*?)```", RegexOption.IGNORE_CASE)
        fence.find(text)?.let { return it.groupValues[1].trim() }
        val doc = Regex("<html[\\s\\S]*?</html>", RegexOption.IGNORE_CASE)
        doc.find(text)?.let { return it.groupValues[0] }
        return null
    }

    /** WebView 全屏预览 HTML 方案（应用内打开）。 */
    private var htmlPreviewDialog: Dialog? = null

    private fun showHtmlPreview(html: String) {
        htmlPreviewDialog?.dismiss()
        val dialog = Dialog(this)
        val web = WebView(applicationContext)
        web.settings.javaScriptEnabled = true
        web.settings.loadWithOverviewMode = true
        web.settings.useWideViewPort = true
        web.loadDataWithBaseURL(null, html, "text/html", "utf-8", null)
        dialog.setContentView(web)
        dialog.window?.setBackgroundDrawable(ColorDrawable(Color.TRANSPARENT))
        dialog.window?.setLayout(
            WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.MATCH_PARENT
        )
        htmlPreviewDialog = dialog
        dialog.setOnDismissListener {
            htmlPreviewDialog = null
            (web.parent as? ViewGroup)?.removeView(web)
            web.destroy()
        }
        dialog.show()
    }

    /* ---------- 附件 ---------- */

    /** 附件选择（文件/图片共用）：复制到当前工作目录，返回真实路径供 reasonix 读取。 */
    private fun addAttachment(uri: Uri, isImage: Boolean) {
        val name = sanitizeFileName(queryDisplayName(uri) ?: "attachment-${System.currentTimeMillis()}")
        val dir = envStore.current()?.directory
            ?.takeIf { it.isNotBlank() }
            ?: settings.defaultWorkspaceDir("default")
        // 重名避免静默覆盖：追加时间戳
        val target = uniqueFile(File(dir, name))
        val ok = try {
            contentResolver.openInputStream(uri)?.use { input ->
                FileOutputStream(target).use { out -> input.copyTo(out) }
            } != null
        } catch (e: Exception) {
            Toast.makeText(this, "附件复制失败：${e.message}", Toast.LENGTH_SHORT).show()
            false
        }
        if (!ok) return
        pendingAttachments.add(ChatAttachment(target.name, if (isImage) Uri.fromFile(target) else null, isImage, target.absolutePath))
        renderAttachments()
        Toast.makeText(this, "已添加附件：${target.name}（已复制到工作目录）", Toast.LENGTH_SHORT).show()
    }

    /** 文件名白名单：仅保留安全字符，杜绝路径注入（/、.. 等）。 */
    private fun sanitizeFileName(raw: String): String {
        val cleaned = raw.replace(Regex("[^\\w.\\-\\u4e00-\\u9fa5]"), "_").trim('_')
        return cleaned.ifBlank { "attachment-${System.currentTimeMillis()}" }.take(64)
    }

    /** 重名时追加序号，避免覆盖。 */
    private fun uniqueFile(f: File): File {
        if (!f.exists()) return f
        val base = f.nameWithoutExtension
        val ext = f.extension
        var i = 1
        while (true) {
            val candidate = File(f.parentFile, "${base}_$i${if (ext.isNotEmpty()) ".$ext" else ""}")
            if (!candidate.exists()) return candidate
            i++
        }
    }

    private fun queryDisplayName(uri: Uri): String? {
        return try {
            contentResolver.query(uri, arrayOf(android.provider.OpenableColumns.DISPLAY_NAME), null, null, null)
                ?.use { c -> if (c.moveToFirst()) c.getString(0) else null }
        } catch (e: Exception) {
            null
        }
    }

    private fun renderAttachments() {
        binding.attachRow.isVisible = pendingAttachments.isNotEmpty()
        binding.llAttachments.removeAllViews()
        pendingAttachments.forEach { a ->
            val chip = TextView(this).apply {
                text = "📎 ${a.name}  ✕"
                setTextColor(ContextCompat.getColor(this@MainActivity, R.color.text_primary))
                textSize = 12f
                setPadding(36, 24, 36, 24)
                gravity = Gravity.CENTER_VERTICAL
                background = ContextCompat.getDrawable(this@MainActivity, R.drawable.bg_chip_seg)
                setOnClickListener {
                    pendingAttachments.remove(a)
                    try { File(a.path).delete() } catch (_: Exception) {}
                    renderAttachments()
                }
            }
            val lp = ViewGroup.MarginLayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            )
            lp.marginEnd = 20
            binding.llAttachments.addView(chip, lp)
        }
    }

    /* ---------- 快捷能力行（真实可用高亮 / 未集成灰置） ---------- */

    /** 工具注册表：仅真实预装（rootfs /opt/ + assets usr/bin）的快捷任务模板，点击填入推荐任务；工具调度由 AI 自行决定。 */
    private data class Tool(val name: String)

    private fun setupToolRow() {
        val tools = listOf(
            Tool("Reasonix"),
            Tool("Argus"),
            Tool("RepoAudit"),
            Tool("PentestGPT"),
            Tool("Tengu"),
            Tool("MCTS"),
            Tool("ghostprobe"),
            Tool("ZAP"),
            Tool("mitmproxy"),
        )
        tools.forEach { t ->
            val chip = TextView(this).apply {
                text = t.name
                textSize = 12f
                setPadding(40, 28, 40, 28)
                gravity = Gravity.CENTER_VERTICAL
                background = ContextCompat.getDrawable(this@MainActivity, R.drawable.bg_chip_work)
                setTextColor(ContextCompat.getColor(this@MainActivity, R.color.text_primary))
                setOnClickListener {
                    binding.etInput.setText(presetTask(t.name))
                    binding.etInput.requestFocus()
                }
            }
            val lp = ViewGroup.MarginLayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            )
            lp.marginEnd = 20
            lp.bottomMargin = 12
            binding.llTools.addView(chip, lp)
        }
    }

    /** 工具推荐任务模板（点击填入输入栏）。 */
    private fun presetTask(name: String): String = when (name) {
        "Reasonix" -> "帮我分析当前项目并给出建议"
        "Argus" -> "对指定目标做 AI 红队测试（OWASP LLM Top10）"
        "RepoAudit" -> "审计当前项目，查找空指针/内存泄漏/UAF"
        "PentestGPT" -> "自动化渗透测试 / CTF 解题"
        "Tengu" -> "自动侦察并生成渗透测试报告"
        "MCTS" -> "扫描 MCP 工具链的注入/权限/攻击链风险"
        "ghostprobe" -> "对 MCP 工具列表做动态安全探测"
        "ZAP" -> "Web 主动扫描（zap-daemon）"
        "mitmproxy" -> "抓包分析指定接口的 HTTP(S) 流量"
        else -> "帮我审计这个项目"
    }

    private fun llmKeyConfigured(): Boolean = settings.envMap().keys.any { it.endsWith("_API_KEY") }

    /* ---------- 抽屉：任务列表 + 设置 ---------- */

    private fun refreshTasks() {
        val tasks = taskStore.all()
        taskAdapter.submit(tasks)
        binding.tvTaskCount.text =
            if (tasks.isEmpty()) "暂无任务" else "${tasks.size} 个任务"
    }

    private fun setupActions() {
        binding.btnMenu.setOnClickListener { binding.drawerLayout.openDrawer(binding.drawer) }
        binding.drawerLayout.addDrawerListener(object : androidx.drawerlayout.widget.DrawerLayout.DrawerListener {
            override fun onDrawerSlide(drawerView: View, slideOffset: Float) {}
            override fun onDrawerOpened(drawerView: View) { refreshTasks() }
            override fun onDrawerClosed(drawerView: View) {}
            override fun onDrawerStateChanged(newState: Int) {}
        })

        binding.btnSettings.setOnClickListener {
            binding.drawerLayout.closeDrawer(binding.drawer)
            startActivity(Intent(this, SettingsActivity::class.java))
        }
        binding.btnAvatar.setOnClickListener {
            binding.drawerLayout.closeDrawer(binding.drawer)
            startActivity(Intent(this, SettingsActivity::class.java))
        }

        // 附件按钮：弹选择菜单（文件 / 图片）
        binding.btnAttach.setOnClickListener {
            if (pendingAttachments.size >= 3) {
                Toast.makeText(this, "最多附加 3 个文件", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            val kinds = arrayOf("📄 文件", "🖼 图片")
            androidx.appcompat.app.AlertDialog.Builder(this)
                .setTitle("添加附件（复制到工作目录，reasonix 可读取）")
                .setItems(kinds) { _, which ->
                    if (which == 0) pickFileLauncher.launch(arrayOf("*/*"))
                    else pickImageLauncher.launch(arrayOf("image/*"))
                }
                .show()
        }

        // 发送
        binding.btnSend.setOnClickListener { sendMessage() }

        // 侧边任务卡：点击切换到任务环境并填入该任务
        taskAdapter = TaskAdapter(taskStore.all()) { task ->
            binding.drawerLayout.closeDrawer(binding.drawer)
            binding.etInput.setText(task.title)
            binding.etInput.requestFocus()
        }
        binding.rvTasks.layoutManager = LinearLayoutManager(this)
        binding.rvTasks.adapter = taskAdapter
    }
}
