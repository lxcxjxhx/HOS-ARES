package com.hos.ares

import android.content.Context
import org.json.JSONArray

/**
 * HOS 任务持久化 —— 以目录为键管理任务卡（目录去重）。
 *
 * 存储于应用私有 SharedPreferences，保存为 JSON 数组。
 */
class TaskStore(context: Context) {

    private val prefs = context.getSharedPreferences("hos_tasks", Context.MODE_PRIVATE)
    private val key = "tasks"

    /** 读取全部任务卡，按最近打开时间倒序。 */
    fun all(): List<Task> {
        val raw = prefs.getString(key, "[]") ?: "[]"
        val arr = JSONArray(raw)
        val list = (0 until arr.length()).map { Task.fromJson(arr.getJSONObject(it)) }
        return list.sortedByDescending { it.lastOpened }
    }

    /** 按目录查找任务卡；不存在返回 null。 */
    fun findByDirectory(directory: String): Task? =
        all().firstOrNull { it.directory == directory }

    /**
     * 获取或创建任务卡：目录已存在则返回已有卡（不新建），
     * 否则创建新卡并落盘。这正是"目录去重"的核心逻辑。
     */
    fun getOrCreate(directory: String, title: String): Task {
        val existing = findByDirectory(directory)
        if (existing != null) {
            return touch(existing.id)
        }
        val task = Task(
            id = java.util.UUID.randomUUID().toString(),
            directory = directory,
            title = title.ifBlank { directory.substringAfterLast('/').ifBlank { directory } },
            createdAt = System.currentTimeMillis(),
            lastOpened = System.currentTimeMillis(),
        )
        save(all() + task)
        return task
    }

    /** 更新任务卡的最后打开时间。 */
    fun touch(id: String): Task {
        val list = all()
        val updated = list.map {
            if (it.id == id) it.copy(lastOpened = System.currentTimeMillis()) else it
        }
        save(updated)
        return updated.first { it.id == id }
    }

    /** 删除任务卡。 */
    fun remove(id: String) {
        save(all().filterNot { it.id == id })
    }

    private fun save(tasks: List<Task>) {
        val arr = JSONArray()
        tasks.forEach { arr.put(it.toJson()) }
        prefs.edit().putString(key, arr.toString()).apply()
    }
}
