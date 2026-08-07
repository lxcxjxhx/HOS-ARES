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

    companion object {
        /** 默认工作目录根路径。 */
        val defaultProjectDir: String = "/sdcard/.ares/project"
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
        get() = prefs.getString("model", "deepseek-chat") ?: "deepseek-chat"
        set(v) = prefs.edit().putString("model", v).apply()

    /** 电脑端 Agent Server 地址（协同模式）。 */
    var serverUrl: String
        get() = prefs.getString("server_url", "") ?: ""
        set(v) = prefs.edit().putString("server_url", v).apply()

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
        if (deepseekKey.isNotBlank()) m["DEEPSEEK_API_KEY2"] = deepseekKey
        if (serverUrl.isNotBlank()) m["HOS_SERVER_URL"] = serverUrl
        m["HOS_BACKEND"] = backend
        m["HOS_MODEL"] = model
        return m
    }
}
