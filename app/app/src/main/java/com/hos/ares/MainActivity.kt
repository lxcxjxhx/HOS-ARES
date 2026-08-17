package com.hos.ares

import android.app.Dialog
import android.content.Intent
import android.graphics.drawable.ColorDrawable
import android.net.ConnectivityManager
import android.net.Network
import android.os.Bundle
import android.view.View
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.documentfile.provider.DocumentFile
import androidx.drawerlayout.widget.DrawerLayout
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import com.hos.ares.databinding.ActivityMainBinding
import com.hos.ares.databinding.DialogAgentDetailBinding
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch

/**
 * HOS 主界面（原创实现）—— 手机端 AI Agent 工作台。
 *
 * 顶栏：HOS 品牌 + 侧边栏入口 + 新建任务。
 * 侧边栏：任务卡列表（按目录去重），底部设置按钮 → 配置界面。
 * 内容区：目录 + 任务输入、执行、流式输出。
 */
class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var gateway: AresGateway
    private lateinit var proot: ProotRuntime
    private lateinit var taskStore: TaskStore
    private lateinit var settings: SettingsStore
    private lateinit var adapter: TaskAdapter
    private lateinit var agentCardAdapter: AgentCardAdapter
    private var runJob: Job? = null

    // 顶栏状态：运行时就绪阶段 + 是否正在分析
    private var runtimeState: RuntimeState = RuntimeState.INITIALIZING
    private var isRunning: Boolean = false

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

    // SAF 目录选择：回调中持久化权限并回填 /sdcard 路径
    private val pickDirLauncher =
        registerForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri ->
            if (uri == null) return@registerForActivityResult
            try {
                contentResolver.takePersistableUriPermission(
                    uri,
                    Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION
                )
            } catch (_: Exception) {
                // 持久化失败不影响本次选择
            }
            val segs = uri.pathSegments
            val treeIdx = segs.indexOf("tree")
            val docId = if (treeIdx >= 0 && treeIdx + 1 < segs.size)
                segs.subList(treeIdx + 1, segs.size).joinToString("/")
            else uri.lastPathSegment ?: ""
            if (docId.startsWith("primary:")) {
                binding.etDirectory.setText("/sdcard/" + docId.removePrefix("primary:"))
            } else {
                Toast.makeText(this, "非主存储，请手动输入路径", Toast.LENGTH_SHORT).show()
            }
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        val proot = ProotRuntime(applicationContext)
        this.proot = proot
        settings = SettingsStore(this)
        taskStore = TaskStore(this)
        gateway = AresGateway(proot) { settings.envMap() }

        // 注册网络监听：联网后自动重试初始化/依赖安装
        try {
            getSystemService(ConnectivityManager::class.java)
                .registerDefaultNetworkCallback(networkCallback)
        } catch (_: Exception) {
            // 网络监听注册失败不影响主流程
        }

        setupTaskList()
        setupAgentCards()
        setupActions()

        // 首次启动在后台自愈式初始化（解包 rootfs / 装依赖 / 自愈），不阻塞主线程
        lifecycleScope.launch { proot.init() }
        lifecycleScope.launch {
            gateway.output.collect { binding.tvOutput.text = it }
        }
        lifecycleScope.launch {
            proot.state.collect { state ->
                runtimeState = state
                updateStatus()
            }
        }
        lifecycleScope.launch {
            gateway.running.collect {
                isRunning = it
                updateStatus()
            }
        }
        lifecycleScope.launch {
            gateway.events.collect { agentCardAdapter.submit(it) }
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        // 注销网络监听，避免泄漏
        try {
            getSystemService(ConnectivityManager::class.java)
                .unregisterNetworkCallback(networkCallback)
        } catch (_: Exception) {
            // 注销失败无副作用
        }
    }

    /** 根据运行时就绪阶段 + 是否正在分析，刷新顶栏状态文案。 */
    private fun updateStatus() {
        binding.tvStatus.text = when {
            isRunning -> getString(R.string.status_running)
            runtimeState == RuntimeState.READY -> getString(R.string.status_ready)
            runtimeState == RuntimeState.NEEDS_NETWORK -> getString(R.string.status_needs_network)
            runtimeState == RuntimeState.ERROR -> getString(R.string.status_error)
            else -> getString(R.string.status_init)
        }
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
                "• 审计 /sdcard/MyApp 找内存泄漏（RepoAudit）\n" +
                "• 对当前项目做 SAST/SCA 全量扫描（Argus）\n" +
                "• 对目标做渗透测试（Strix）\n" +
                "• 深度综合审计（Reasonix 多 Agent 自循环，无需逐轮确认）\n\n" +
                "支持上传文件/图片作为上下文。点击下方快捷行可一键填入常见任务模板。")
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

        pendingAttachments.clear()
        renderAttachments()
        binding.etInput.setText("")

        runTask(taskText, dir)
    }

    /**
     * 执行任务（用户发送 / 定时任务共用）：状态行 + AI 占位 + reasonix 无人值守运行。
     * 自循环 / YOLO 开关经 SettingsStore.envMap() 注入，ProotRuntime 拼参数。
     */
    private fun runTask(taskText: String, dir: String) {
        if (running) {
            Toast.makeText(this, "上一任务仍在运行，请等待完成或稍后再试", Toast.LENGTH_SHORT).show()
            return
        }
        if (!llmKeyConfigured()) {
            Toast.makeText(this, "未配置 LLM Key：请先到设置中配置 DeepSeek Key（点击下方「一键填入 DeepSeek 默认配置」）", Toast.LENGTH_LONG).show()
            startActivity(Intent(this, SettingsActivity::class.java))
            return
        }
        if (runtimeState != RuntimeState.READY) {
            val msg = when (runtimeState) {
                RuntimeState.NEEDS_NETWORK -> "运行环境需要联网（首次使用需安装 Python 依赖），请连接网络后重试"
                RuntimeState.ERROR -> "运行环境初始化失败，请清除应用数据后重新启动"
                else -> "运行环境未就绪，请稍候或联网后重试"
            }
            Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()
            return
        }

        // 状态行 + AI 占位
        adapter.add(ChatMessage(ChatType.STATUS, "调度 reasonix 统一入口 …"))

        var progressMsg = ChatMessage(ChatType.PROGRESS, events = emptyList())
        adapter.add(progressMsg)

        var aiMsg = ChatMessage(ChatType.AI, "")
        adapter.add(aiMsg)
        scrollToBottom()

        running = true
        updateStatus()

        val gw = AresGateway(proot) { settings.envMap() }
        runJob = lifecycleScope.launch {
            val outCollect = launch {
                gw.output.collect { full ->
                    // 流式：全文替换当前 AI 气泡（续传新实例保持定位有效）
                    aiMsg = adapter.replace(aiMsg, full)
                    scrollToBottom()
                }
            }
            val eventsCollect = launch {
                gw.events.collect { evs ->
                    progressMsg = adapter.replace(progressMsg, "", ChatType.PROGRESS, null, evs)
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
                if (!isDestroyed && !isFinishing) {
                    val humanized = proot.humanizeError(e.message ?: "未知错误")
                    adapter.replace(aiMsg, "✗ 出错：$humanized")
                }
            } finally {
                outCollect.cancel()
                eventsCollect.cancel()
                running = false
                if (!isDestroyed && !isFinishing) updateStatus()
            }
        }
    }

    /** 结果落地：写入 AI 气泡；若输出含 HTML 方案，转为 HTML 卡片气泡。返回替换后的消息。 */
    private fun handleResult(aiMsg: ChatMessage, finalText: String, success: Boolean): ChatMessage {
        if (!success) {
            val humanized = proot.humanizeError(finalText)
            return adapter.replace(aiMsg, "✗ 执行失败\n$humanized")
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
            Tool("Strix"),
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
        "Reasonix" -> "帮我深度审计当前项目（综合多 Agent 自循环，无需逐轮确认）"
        "Argus" -> "对当前项目做 SAST/SCA/Secrets/IaC 全量扫描（无需 AI）"
        "RepoAudit" -> "审计当前项目，查找空指针/内存泄漏/UAF 等符号可达漏洞"
        "Strix" -> "对目标做渗透测试（红队/CTF），自动枚举并生成利用链与修复建议"
        else -> "帮我审计这个项目"
    }

    private fun llmKeyConfigured(): Boolean = settings.envMap().keys.any { it.endsWith("_API_KEY") }

    /* ---------- 抽屉：任务列表 + 设置 ---------- */

    private fun refreshTasks() {
        val tasks = taskStore.all()
        adapter.submit(tasks)
        binding.tvTaskCount.text = if (tasks.isEmpty()) "暂无任务，在目录下新建任务后自动创建任务卡" else "${tasks.size} 个任务"
    }

    private fun setupAgentCards() {
        binding.rvAgentCards.layoutManager = LinearLayoutManager(this, LinearLayoutManager.HORIZONTAL, false)
        agentCardAdapter = AgentCardAdapter(emptyList()) { e -> showAgentDetail(e) }
        binding.rvAgentCards.adapter = agentCardAdapter
    }

    private fun showAgentDetail(event: AgentRunEvent) {
        val dialog = Dialog(this)
        val b = DialogAgentDetailBinding.inflate(layoutInflater)
        dialog.setContentView(b.root)
        dialog.window?.setBackgroundDrawable(ColorDrawable(android.graphics.Color.TRANSPARENT))

        b.tvDetailTitle.text = event.skill
        val statusText = when (event.status) {
            AgentStatus.PENDING -> "等待中"
            AgentStatus.RUNNING -> "运行中"
            AgentStatus.DONE -> "完成"
            AgentStatus.FAILED -> "失败"
            AgentStatus.CANCELLED -> "已取消"
            AgentStatus.TIMEOUT -> "超时"
        }
        val statusColor = when (event.status) {
            AgentStatus.PENDING -> R.color.text_muted
            AgentStatus.RUNNING -> R.color.accent
            AgentStatus.DONE -> R.color.accent_green
            AgentStatus.FAILED -> R.color.error
            AgentStatus.CANCELLED -> R.color.text_muted
            AgentStatus.TIMEOUT -> R.color.accent_red
        }
        b.tvDetailStatus.text = statusText
        b.tvDetailStatus.setTextColor(ContextCompat.getColor(this, statusColor))
        b.tvDetail.text = event.detail.ifEmpty { "（暂无输出）" }

        // 实时流式：订阅网关事件，持续刷新该 Agent 的状态与详细输出
        val skillName = b.tvDetailTitle.text.toString()
        val collectJob = lifecycleScope.launch {
            gateway.events.collect { evList ->
                evList.firstOrNull { it.skill == skillName }?.let { live ->
                    b.tvDetailStatus.text = when (live.status) {
                        AgentStatus.PENDING -> "等待中"
                        AgentStatus.RUNNING -> "运行中"
                        AgentStatus.DONE -> "完成"
                        AgentStatus.FAILED -> "失败"
                        AgentStatus.CANCELLED -> "已取消"
                        AgentStatus.TIMEOUT -> "超时"
                    }
                    b.tvDetailStatus.setTextColor(ContextCompat.getColor(this@MainActivity, when (live.status) {
                        AgentStatus.PENDING -> R.color.text_muted
                        AgentStatus.RUNNING -> R.color.accent
                        AgentStatus.DONE -> R.color.accent_green
                        AgentStatus.FAILED -> R.color.error
                        AgentStatus.CANCELLED -> R.color.text_muted
                        AgentStatus.TIMEOUT -> R.color.accent_red
                    }))
                    b.tvDetail.text = live.detail.ifEmpty { "（暂无输出）" }
                }
            }
        }
        b.btnClose.setOnClickListener { dialog.dismiss() }
        dialog.setOnDismissListener { collectJob.cancel() }
        dialog.show()
    }

    private fun setupActions() {
        binding.btnMenu.setOnClickListener { binding.drawerLayout.openDrawer(binding.drawer) }
        binding.drawerLayout.addDrawerListener(object : DrawerLayout.DrawerListener {
            override fun onDrawerSlide(drawerView: android.view.View, slideOffset: Float) {}
            override fun onDrawerOpened(drawerView: android.view.View) { refreshTasks() }
            override fun onDrawerClosed(drawerView: android.view.View) {}
            override fun onDrawerStateChanged(newState: Int) {}
        })

        // 新建任务：清空输入，聚焦目录
        binding.btnNewTask.setOnClickListener {
            binding.etDirectory.setText("")
            binding.etTask.setText("")
            binding.drawerLayout.closeDrawer(binding.drawer)
            binding.etDirectory.requestFocus()
        }

        // 图形化选择工作目录（SAF）
        binding.btnPickDir.setOnClickListener { pickDirLauncher.launch(null) }

        // 底部设置按钮 → 配置界面
        binding.btnSettings.setOnClickListener {
            binding.drawerLayout.closeDrawer(binding.drawer)
            startActivity(Intent(this, SettingsActivity::class.java))
        }

        binding.btnRun.setOnClickListener {
            var directory = binding.etDirectory.text.toString().trim()
            val task = binding.etTask.text.toString().trim()
            if (task.isEmpty()) {
                Toast.makeText(this, "请输入任务，例如：帮我审计这个项目", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            // 目录为空时用任务文本自动生成默认工作目录并回填
            if (directory.isEmpty()) {
                directory = settings.defaultWorkspaceDir(task)
                binding.etDirectory.setText(directory)
            }
            // 核心：目录去重 —— 存在则该目录任务卡直接复用，否则新建
            val current = taskStore.getOrCreate(directory, task)
            refreshTasks()
            binding.tvOutput.text = ""
            runJob = lifecycleScope.launch {
                binding.btnRun.isEnabled = false
                binding.btnCancel.visibility = View.VISIBLE
                gateway.run(task, directory, 15 * 60 * 1000L)
                binding.btnRun.isEnabled = true
                binding.btnCancel.visibility = View.GONE
            }
        }

        binding.btnCancel.setOnClickListener {
            runJob?.cancel()
            binding.btnRun.isEnabled = true
            binding.btnCancel.visibility = View.GONE
        }
    }
}
