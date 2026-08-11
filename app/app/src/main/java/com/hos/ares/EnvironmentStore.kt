package com.hos.ares

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

/**
 * HOS 任务环境持久化 —— 用户可维护多个「任务环境」。
 *
 * 每个环境 = 一个工作目录 + 场景备注（如 VPN 渗透测试、涉密信息导入等），
 * 让用户在指定目录下执行任务时可快速切换，避免每次手动输入路径。
 */
data class Environment(
    val id: String,
    val name: String,        // 环境名（如「渗透测试-VPN」）
    val directory: String,   // 工作目录（/sdcard/...）
    val note: String,        // 场景备注（如「搭载 VPN 对目标网段渗透」）
    val lastUsed: Long,
) {

    fun toJson(): JSONObject = JSONObject().apply {
        put("id", id)
        put("name", name)
        put("directory", directory)
        put("note", note)
        put("lastUsed", lastUsed)
    }

    companion object {
        fun fromJson(o: JSONObject): Environment = Environment(
            id = o.optString("id", ""),
            name = o.optString("name", ""),
            directory = o.optString("directory", ""),
            note = o.optString("note", ""),
            lastUsed = o.optLong("lastUsed", 0L),
        )
    }
}

/**
 * 任务环境存储：以 SharedPreferences 持久化环境列表 + 当前选中环境。
 */
class EnvironmentStore(context: Context) {

    private val prefs = context.getSharedPreferences("hos_environments", Context.MODE_PRIVATE)
    private val key = "environments"
    private val keyCurrent = "current_env_id"

    /** 全部环境，按最后使用时间倒序。 */
    fun all(): List<Environment> {
        val raw = prefs.getString(key, "[]") ?: "[]"
        val arr = JSONArray(raw)
        val list = (0 until arr.length()).map { Environment.fromJson(arr.getJSONObject(it)) }
        return list.sortedByDescending { it.lastUsed }
    }

    /** 当前选中的环境；无则返回 null（调用方回退默认目录）。 */
    fun current(): Environment? {
        val id = prefs.getString(keyCurrent, "") ?: ""
        if (id.isBlank()) return null
        return all().firstOrNull { it.id == id } ?: all().firstOrNull()
    }

    /** 设置当前环境（同时刷新 lastUsed）。 */
    fun setCurrent(id: String) {
        val list = all().map {
            if (it.id == id) it.copy(lastUsed = System.currentTimeMillis()) else it
        }
        save(list)
        prefs.edit().putString(keyCurrent, id).apply()
    }

    /** 新建或更新环境：目录已存在则更新名称/备注（目录去重）。 */
    fun upsert(name: String, directory: String, note: String): Environment {
        val dir = directory.trim()
        val list = all()
        val existing = list.firstOrNull { it.directory == dir }
        val env = existing?.copy(
            name = name.ifBlank { existing.name },
            note = note,
            lastUsed = System.currentTimeMillis(),
        ) ?: Environment(
            id = java.util.UUID.randomUUID().toString(),
            name = name.ifBlank { dir.substringAfterLast('/').ifBlank { "默认环境" } },
            directory = dir,
            note = note,
            lastUsed = System.currentTimeMillis(),
        )
        save(list.filterNot { it.id == env.id } + env)
        prefs.edit().putString(keyCurrent, env.id).apply()
        return env
    }

    /** 删除环境；若删除的是当前环境，自动切换到最近使用的一个。 */
    fun remove(id: String) {
        val rest = all().filterNot { it.id == id }
        save(rest)
        if (prefs.getString(keyCurrent, "") == id) {
            prefs.edit().putString(keyCurrent, rest.firstOrNull()?.id ?: "").apply()
        }
    }

    private fun save(environments: List<Environment>) {
        val arr = JSONArray()
        environments.forEach { arr.put(it.toJson()) }
        prefs.edit().putString(key, arr.toString()).apply()
    }
}
