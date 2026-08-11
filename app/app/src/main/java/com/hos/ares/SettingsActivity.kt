package com.hos.ares

import android.os.Bundle
import android.widget.ArrayAdapter
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.hos.ares.databinding.ActivitySettingsBinding

/**
 * HOS LLM 配置页 —— 后端 / Key / 模型 / 协同服务端（下拉框方案）。
 *
 * 后端下拉含 local；提供商下拉切换 4 家 Key，切换时自动暂存当前输入，
 * 页面一次只显示一个 Key 输入框（不叠行）。
 * 顶栏健康度徽章：任一 API Key 已填即绿。
 *
 * 关键点：AutoCompleteTextView 的 item 点击回调在文本已替换为新值后触发，
 * 因此用 lastProvider 成员追踪"当前输入框正属于哪家"，切换/保存都基于它暂存。
 */
class SettingsActivity : AppCompatActivity() {

    private lateinit var binding: ActivitySettingsBinding
    private lateinit var settings: SettingsStore

    private val backends = arrayOf("deepseek", "claude", "openai", "gemini", "local")
    private val backendLabels = arrayOf("DeepSeek", "Claude", "OpenAI", "Gemini", "本地模型")

    // 提供商（4 家）→ 对应存储字段读写
    private val providerLabels = arrayOf("DeepSeek", "Anthropic", "OpenAI", "Gemini")

    // 各提供商 Key 的暂存（切换下拉时保存，避免丢失）
    private val keyBuffer = HashMap<String, String>()

    /** 当前输入框正属于哪家提供商（回调时序安全的关键）。 */
    private var lastProvider: String = "DeepSeek"

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivitySettingsBinding.inflate(layoutInflater)
        setContentView(binding.root)

        settings = SettingsStore(this)

        // 后端下拉
        val backendAdapter = ArrayAdapter(this, android.R.layout.simple_list_item_1, backendLabels)
        binding.spBackend.setAdapter(backendAdapter)
        binding.spBackend.keyListener = null // 仅下拉选择，禁手输
        val bi = backends.indexOfFirst { it == settings.backend }.coerceAtLeast(0)
        binding.spBackend.setText(backendLabels[bi], false)
        binding.spBackend.setOnItemClickListener { _, _, pos, _ ->
            // 切换后端不改变当前编辑的提供商，仅暂存当前输入（防丢）
            keyBuffer[lastProvider] = binding.etKey.text.toString()
            updateHealth()
        }

        // 提供商下拉（默认当前后端对应的提供商）
        val providerAdapter = ArrayAdapter(this, android.R.layout.simple_list_item_1, providerLabels)
        binding.spProvider.setAdapter(providerAdapter)
        binding.spProvider.keyListener = null // 仅下拉选择，禁手输
        lastProvider = defaultProvider(settings.backend)
        val pi = providerLabels.indexOfFirst { it == lastProvider }.coerceAtLeast(0)
        binding.spProvider.setText(providerLabels[pi], false)
        binding.spProvider.setOnItemClickListener { _, _, pos, _ ->
            // 回调时文本已变为新提供商：先按 lastProvider 暂存旧输入，再切换到目标
            keyBuffer[lastProvider] = binding.etKey.text.toString()
            lastProvider = providerLabels[pos]
            binding.etKey.setText(loadKey(lastProvider))
            updateHealth()
        }

        // 回填当前提供商 Key
        binding.etKey.setText(loadKey(lastProvider))

        // 其他字段回填
        binding.etModel.setText(settings.model)
        binding.etServer.setText(settings.serverUrl)
        binding.swSelfLoop.isChecked = settings.selfLoop
        binding.swYolo.isChecked = settings.yolo
        binding.swSchedule.isChecked = settings.scheduleEnabled
        binding.etScheduleTask.setText(settings.scheduleTask)
        binding.etScheduleInterval.setText(settings.scheduleIntervalHours.toString())

        binding.btnBack.setOnClickListener { finish() }

        binding.btnDeepseekPreset.setOnClickListener {
            // 暂存当前输入 → 切到 DeepSeek
            keyBuffer[lastProvider] = binding.etKey.text.toString()
            lastProvider = "DeepSeek"
            binding.spBackend.setText("DeepSeek", false)
            binding.spProvider.setText("DeepSeek", false)
            binding.etKey.setText(loadKey("DeepSeek"))
            binding.etModel.setText("deepseek-v4-flash")
            binding.tvSaveHint.text = "已填入 DeepSeek 默认配置，请填入你的 API Key 后保存"
            updateHealth()
        }

        binding.btnSave.setOnClickListener {
            // 暂存当前输入（trim 归一）
            keyBuffer[lastProvider] = binding.etKey.text.toString().trim()

            // 后端：下拉值（含 local）
            val bi = backendLabels.indexOfFirst { it == binding.spBackend.text.toString() }
            if (bi >= 0) settings.backend = backends[bi]

            settings.anthropicKey = keyBuffer["Anthropic"] ?: settings.anthropicKey
            settings.openaiKey = keyBuffer["OpenAI"] ?: settings.openaiKey
            settings.geminiKey = keyBuffer["Gemini"] ?: settings.geminiKey
            settings.deepseekKey = keyBuffer["DeepSeek"] ?: settings.deepseekKey
            settings.model = binding.etModel.text.toString().trim()
            settings.serverUrl = binding.etServer.text.toString().trim()
            settings.selfLoop = binding.swSelfLoop.isChecked
            settings.yolo = binding.swYolo.isChecked
            settings.scheduleEnabled = binding.swSchedule.isChecked
            settings.scheduleTask = binding.etScheduleTask.text.toString().trim()
            settings.scheduleIntervalHours =
                binding.etScheduleInterval.text.toString().toIntOrNull()?.coerceIn(1, 720) ?: 24
            binding.tvSaveHint.text = "已保存 ✓"
            updateHealth()
            Toast.makeText(this, "设置已保存", Toast.LENGTH_SHORT).show()
        }

        binding.etKey.addTextChangedListener(healthWatcher)
        binding.etModel.addTextChangedListener(healthWatcher)
        binding.etServer.addTextChangedListener(healthWatcher)

        updateHealth()
    }

    /** 后端名 → 默认编辑的提供商。 */
    private fun defaultProvider(backend: String): String = when (backend) {
        "claude" -> "Anthropic"
        "openai" -> "OpenAI"
        "gemini" -> "Gemini"
        else -> "DeepSeek"
    }

    private fun loadKey(provider: String): String = when (provider) {
        "Anthropic" -> keyBuffer["Anthropic"] ?: settings.anthropicKey
        "OpenAI" -> keyBuffer["OpenAI"] ?: settings.openaiKey
        "Gemini" -> keyBuffer["Gemini"] ?: settings.geminiKey
        else -> keyBuffer["DeepSeek"] ?: settings.deepseekKey
    }

    private val healthWatcher = object : android.text.TextWatcher {
        override fun beforeTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
        override fun onTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
        override fun afterTextChanged(s: android.text.Editable?) = updateHealth()
    }

    private fun updateHealth() {
        // 实时读：当前输入 + 已暂存的其他提供商
        val currentInput = binding.etKey.text.toString()
        val keys = listOf(
            if (lastProvider == "DeepSeek") currentInput else keyBuffer["DeepSeek"] ?: settings.deepseekKey,
            if (lastProvider == "Anthropic") currentInput else keyBuffer["Anthropic"] ?: settings.anthropicKey,
            if (lastProvider == "OpenAI") currentInput else keyBuffer["OpenAI"] ?: settings.openaiKey,
            if (lastProvider == "Gemini") currentInput else keyBuffer["Gemini"] ?: settings.geminiKey,
        )
        val curIdx = providerLabels.indexOfFirst { it == lastProvider }.coerceAtLeast(0)
        val filled = keys.map { it.isNotEmpty() }
        binding.dotProvider.setBackgroundResource(
            if (filled[curIdx]) R.drawable.bg_dot_ready else R.drawable.bg_dot_init
        )
        binding.tvProviderSub.text = if (filled[curIdx]) "已配置" else "未配置"

        val any = filled.any { it }
        binding.healthBadge.setBackgroundResource(if (any) R.drawable.bg_dot_ready else R.drawable.bg_dot_init)
        binding.tvHealth.setTextColor(
            ContextCompat.getColor(this, if (any) R.color.text_primary else R.color.text_muted)
        )
        binding.tvHealth.text = if (any) "✓" else "·"
    }
}
