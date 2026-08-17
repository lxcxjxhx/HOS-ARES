package com.hos.ares

import android.os.Bundle
import android.widget.ArrayAdapter
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.hos.ares.databinding.ActivitySettingsBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

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
            binding.etModel.setText("deepseek-v4-flash")
            // 一键填充也把 llmBaseUrl 设为官方默认（兜底 SettingsStore.migrate，同时便于 envMap 导出）
            settings.llmBaseUrl = "https://api.deepseek.com"
            binding.tvSaveHint.text = "已填入 DeepSeek 默认配置，请填入你的 API Key 后保存"
        }

        binding.btnTestConn.setOnClickListener {
            val key = binding.etKey.text.toString().trim()
            if (key.isBlank()) {
                binding.tvSaveHint.text = "请先填入 API Key"
                return@setOnClickListener
            }
            binding.btnTestConn.isEnabled = false
            binding.tvSaveHint.text = "测试中..."

            lifecycleScope.launch(Dispatchers.IO) {
                try {
                    val baseUrl = settings.llmBaseUrl.trimEnd('/')
                    val model = binding.etModel.text.toString().trim().ifEmpty { "deepseek-chat" }
                    val url = java.net.URL("$baseUrl/v1/chat/completions")
                    val conn = (url.openConnection() as java.net.HttpURLConnection).apply {
                        requestMethod = "POST"
                        connectTimeout = 10000
                        readTimeout = 15000
                        setRequestProperty("Content-Type", "application/json")
                        setRequestProperty("Authorization", "Bearer $key")
                        doOutput = true
                    }
                    val body = """{"model":"$model","messages":[{"role":"user","content":"hi"}],"max_tokens":1}"""
                    conn.outputStream.use { it.write(body.toByteArray()) }
                    val code = conn.responseCode
                    val resp = if (code in 200..299) {
                        conn.inputStream.bufferedReader().use { it.readText() }
                    } else {
                        conn.errorStream?.bufferedReader()?.use { it.readText() } ?: "HTTP $code"
                    }
                    withContext(Dispatchers.Main) {
                        binding.btnTestConn.isEnabled = true
                        if (code in 200..299) {
                            binding.tvSaveHint.text = "✓ 连通正常 (HTTP $code)"
                        } else {
                            val errMsg = resp.take(200)
                            val diagnostic = when (code) {
                                401 -> "API Key 无效或已过期"
                                403 -> "无权访问此模型或 API"
                                404 -> "Endpoint 不存在，请检查 Base URL"
                                429 -> "请求过于频繁，触发限流"
                                500 -> "服务端错误，请稍后重试"
                                502, 503, 504 -> "服务暂时不可用"
                                else -> "HTTP $code"
                            }
                            binding.tvSaveHint.text = "✗ 失败 ($diagnostic): $errMsg"
                        }
                    }
                } catch (e: Exception) {
                    withContext(Dispatchers.Main) {
                        binding.btnTestConn.isEnabled = true
                        val msg = e.message ?: "未知错误"
                        val hint = when {
                            "UnknownHost" in msg || "DNS" in msg -> "DNS 解析失败，请检查网络连接或 Base URL"
                            "ConnectException" in msg || "Connection refused" in msg -> "无法连接到服务器，请检查 Base URL 是否正确"
                            "SocketTimeout" in msg -> "连接超时，请检查网络或服务器是否可达"
                            "SSL" in msg || "ssl" in msg -> "SSL 证书错误，请检查 Base URL 是否使用 HTTPS"
                            else -> msg
                        }
                        binding.tvSaveHint.text = "✗ 连接失败: $hint"
                    }
                }
            }
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
