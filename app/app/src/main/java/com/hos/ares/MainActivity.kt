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

    private fun setupTaskList() {
        binding.rvTasks.layoutManager = LinearLayoutManager(this)
        adapter = TaskAdapter(taskStore.all()) { task ->
            // 点击任务卡：跳转到对应目录的任务
            binding.etDirectory.setText(task.directory)
            binding.etTask.setText("")
            taskStore.touch(task.id)
            refreshTasks()
            binding.drawerLayout.closeDrawer(binding.drawer)
            binding.etTask.requestFocus()
        }
        binding.rvTasks.adapter = adapter
        refreshTasks()
    }

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
