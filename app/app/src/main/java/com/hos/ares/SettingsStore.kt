package com.hos.ares

import android.content.Context

/**
 * HOS 设置持久化 —— LLM Key / 服务端地址 / 模型 / 主题等。
 *
 * 配置界面（SettingsActivity）读写这些值；运行 Agent 时，
 * ProotRuntime 会把其中相关项注入为 rootfs 内环境变量。
 */
class SettingsStore(context: Context) {

    private val prefs = context.getSharedPreferences("hos_settings", Context.MODE_PRIVATE)

    init {
        migrate()
    }

    companion object {
        /** 默认工作目录根路径。 */
        val defaultProjectDir: String = "/sdcard/.ares/project"

        /** 当前设置 schema 版本。升级时若需要迁移旧默认值，递增此值并补充迁移分支。 */
        private const val SETTINGS_VERSION = 1
        private const val KEY_SETTINGS_VERSION = "settings_version"
    }

    /**
     * 设置项自愈迁移：检测到旧版本（version 缺失/较低）时，把已持久化的旧默认值
     * （deepseek-chat / dsv4flash 等）归一化为当前默认值，避免用户升级后手动清数据。
     * 幂等，仅在版本变化时执行一次。
     */
    private fun migrate() {
        val stored = prefs.getInt(KEY_SETTINGS_VERSION, 0)
        if (stored >= SETTINGS_VERSION) return
        val editor = prefs.edit()
        // 旧默认模型名（历史版本曾用）→ 归一化为当前默认
        val oldModel = prefs.getString("model", "").orEmpty()
        if (oldModel == "deepseek-chat" || oldModel == "dsv4flash") {
            editor.putString("model", "deepseek-v4-flash")
        }
        // 旧 base URL 为空 → 回填默认
        if (prefs.getString("llm_base_url", "").isNullOrBlank()) {
            editor.putString("llm_base_url", "https://api.deepseek.com")
        }
        editor.putInt(KEY_SETTINGS_VERSION, SETTINGS_VERSION).apply()
    }

    /**
     * 生成默认工作目录：优先使用 name（安全化后），否则用随机数兜底，保证唯一。
     * 返回路径始终以 /sdcard/.ares/project/ 为前缀。
     */
    fun defaultWorkspaceDir(name: String? = null): String {
        val safe = sanitizeName(name)
        val folder = if (safe != null) safe else "project-" + (100000..999999).random()
        return "$defaultProjectDir/$folder"
    }

    /** 过滤非法字符并截断到 40 位；过滤后为空返回 null，由调用方走随机数兜底。 */
    private fun sanitizeName(name: String?): String? {
        if (name.isNullOrBlank()) return null
        val cleaned = name
            .map { c -> if (c in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-") c else '-' }
            .joinToString("")
            .trim('-')
        if (cleaned.isEmpty()) return null
        return cleaned.take(40)
    }

    var anthropicKey: String
        get() = prefs.getString("anthropic_key", "") ?: ""
        set(v) = prefs.edit().putString("anthropic_key", v).apply()

    var openaiKey: String
        get() = prefs.getString("openai_key", "") ?: ""
        set(v) = prefs.edit().putString("openai_key", v).apply()

    var geminiKey: String
        get() = prefs.getString("gemini_key", "") ?: ""
        set(v) = prefs.edit().putString("gemini_key", v).apply()

    var deepseekKey: String
        get() = prefs.getString("deepseek_key", "") ?: ""
        set(v) = prefs.edit().putString("deepseek_key", v).apply()

    /** 默认后端（deepseek / claude / openai / gemini / local）。 */
    var backend: String
        get() = prefs.getString("backend", "deepseek") ?: "deepseek"
        set(v) = prefs.edit().putString("backend", v).apply()

    /** 模型名。 */
    var model: String
        get() = prefs.getString("model", "deepseek-v4-flash") ?: "deepseek-v4-flash"
        set(v) = prefs.edit().putString("model", v).apply()

    /** 电脑端 Agent Server 地址（协同模式）。 */
    var serverUrl: String
        get() = prefs.getString("server_url", "") ?: ""
        set(v) = prefs.edit().putString("server_url", v).apply()

    /** LLM API Base URL（reasonix/agent 入口通过 HOS_LLM_BASE_URL 读取）。 */
    var llmBaseUrl: String
        get() = prefs.getString("llm_base_url", "https://api.deepseek.com") ?: "https://api.deepseek.com"
        set(v) = prefs.edit().putString("llm_base_url", v).apply()

    /** 是否已联网安装依赖（用于提示）。 */
    var bootstrapWarned: Boolean
        get() = prefs.getBoolean("bootstrap_warned", false)
        set(v) = prefs.edit().putBoolean("bootstrap_warned", v).apply()

    /** 构建注入 rootfs 的环境变量表。 */
    fun envMap(): Map<String, String> {
        val m = HashMap<String, String>()
        if (anthropicKey.isNotBlank()) m["ANTHROPIC_API_KEY"] = anthropicKey
        if (openaiKey.isNotBlank()) m["OPENAI_API_KEY"] = openaiKey
        if (geminiKey.isNotBlank()) m["GOOGLE_API_KEY"] = geminiKey
        if (deepseekKey.isNotBlank()) m["DEEPSEEK_API_KEY"] = deepseekKey
        if (deepseekKey.isNotBlank()) m["DEEPSEEK_API_KEY2"] = deepseekKey
        if (serverUrl.isNotBlank()) m["HOS_SERVER_URL"] = serverUrl
        m["HOS_BACKEND"] = backend
        m["HOS_MODEL"] = model
        m["HOS_LLM_BASE_URL"] = llmBaseUrl
        return m
    }
}
