package com.hos.ares

import android.content.Intent
import android.net.ConnectivityManager
import android.net.Network
import android.os.Bundle
import android.view.View
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.drawerlayout.widget.DrawerLayout
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import com.hos.ares.databinding.ActivityMainBinding
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var gateway: AresGateway
    private lateinit var proot: ProotRuntime
    private lateinit var taskStore: TaskStore
    private lateinit var settings: SettingsStore
    private lateinit var adapter: TaskAdapter
    private lateinit var agentCardAdapter: AgentCardAdapter
    private var runJob: Job? = null

    private var runtimeState: RuntimeState = RuntimeState.INITIALIZING
    private var isRunning: Boolean = false

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

    private val pickDirLauncher =
        registerForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri ->
            if (uri == null) return@registerForActivityResult
            try {
                contentResolver.takePersistableUriPermission(
                    uri,
                    Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION
                )
            } catch (_: Exception) {
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

        try {
            getSystemService(ConnectivityManager::class.java)
                .registerDefaultNetworkCallback(networkCallback)
        } catch (_: Exception) {
        }

        setupTaskList()
        setupAgentCards()
        setupActions()

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
        try {
            getSystemService(ConnectivityManager::class.java)
                .unregisterNetworkCallback(networkCallback)
        } catch (_: Exception) {
        }
    }

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
        adapter = TaskAdapter(emptyList()) { task ->
            binding.etDirectory.setText(task.directory)
            binding.etTask.setText(task.title)
            binding.drawerLayout.closeDrawer(binding.drawer)
        }
        binding.rvTasks.layoutManager = LinearLayoutManager(this)
        binding.rvTasks.adapter = adapter
    }

    private fun setupAgentCards() {
        binding.rvAgentCards.layoutManager = LinearLayoutManager(this, LinearLayoutManager.HORIZONTAL, false)
        agentCardAdapter = AgentCardAdapter(emptyList()) { e -> showAgentDetail(e) }
        binding.rvAgentCards.adapter = agentCardAdapter
    }

    private fun showAgentDetail(event: AgentRunEvent) {
        val dialog = android.app.Dialog(this)
        val b = com.hos.ares.databinding.DialogAgentDetailBinding.inflate(layoutInflater)
        dialog.setContentView(b.root)
        dialog.window?.setBackgroundDrawable(android.graphics.drawable.ColorDrawable(android.graphics.Color.TRANSPARENT))

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
        b.tvDetailStatus.setTextColor(androidx.core.content.ContextCompat.getColor(this, statusColor))
        b.tvDetail.text = event.detail.ifEmpty { "（暂无输出）" }

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
                    b.tvDetailStatus.setTextColor(androidx.core.content.ContextCompat.getColor(this@MainActivity, when (live.status) {
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

        binding.btnNewTask.setOnClickListener {
            binding.etDirectory.setText("")
            binding.etTask.setText("")
            binding.drawerLayout.closeDrawer(binding.drawer)
            binding.etDirectory.requestFocus()
        }

        binding.btnPickDir.setOnClickListener { pickDirLauncher.launch(null) }

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
            if (directory.isEmpty()) {
                directory = settings.defaultWorkspaceDir(task)
                binding.etDirectory.setText(directory)
            }
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

    private fun refreshTasks() {
        val tasks = taskStore.all()
        adapter.submit(tasks)
        binding.tvTaskCount.text = if (tasks.isEmpty()) "暂无任务" else "${tasks.size} 个任务"
    }
}