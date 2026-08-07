package com.hos.ares

import android.os.Bundle
import android.widget.ArrayAdapter
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.hos.ares.databinding.ActivitySettingsBinding

/**
 * HOS 配置界面 —— 轻松配置 LLM Key / 后端 / 模型 / 协同服务端。
 *
 * 保存后由 SettingsStore 持久化，并在运行 Agent 时注入 rootfs 环境变量。
 */
class SettingsActivity : AppCompatActivity() {

    private lateinit var binding: ActivitySettingsBinding
    private lateinit var settings: SettingsStore

    private val backends = arrayOf("deepseek", "claude", "openai", "gemini", "local")

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivitySettingsBinding.inflate(layoutInflater)
        setContentView(binding.root)

        settings = SettingsStore(this)

        // 后端下拉
        binding.spBackend.adapter = ArrayAdapter(
            this, android.R.layout.simple_spinner_dropdown_item, backends
        )
        val idx = backends.indexOfFirst { it == settings.backend }.coerceAtLeast(0)
        binding.spBackend.setSelection(idx)

        // 回填已保存的值
        binding.etAnthropic.setText(settings.anthropicKey)
        binding.etOpenai.setText(settings.openaiKey)
        binding.etGemini.setText(settings.geminiKey)
        binding.etDeepseek.setText(settings.deepseekKey)
        binding.etModel.setText(settings.model)
        binding.etServer.setText(settings.serverUrl)

        binding.btnBack.setOnClickListener { finish() }

        binding.btnDeepseekPreset.setOnClickListener {
            binding.spBackend.setSelection(0)
            binding.etModel.setText("deepseek-chat")
            binding.tvSaveHint.text = "已填入 DeepSeek 默认配置，请填入你的 API Key 后保存"
        }

        binding.btnSave.setOnClickListener {
            settings.backend = backends[binding.spBackend.selectedItemPosition]
            settings.anthropicKey = binding.etAnthropic.text.toString().trim()
            settings.openaiKey = binding.etOpenai.text.toString().trim()
            settings.geminiKey = binding.etGemini.text.toString().trim()
            settings.deepseekKey = binding.etDeepseek.text.toString().trim()
            settings.model = binding.etModel.text.toString().trim()
            settings.serverUrl = binding.etServer.text.toString().trim()
            binding.tvSaveHint.text = "已保存 ✓"
            Toast.makeText(this, "设置已保存", Toast.LENGTH_SHORT).show()
        }
    }
}
