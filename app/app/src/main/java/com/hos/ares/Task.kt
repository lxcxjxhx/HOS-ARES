package com.hos.ares

import org.json.JSONObject

/**
 * HOS 任务卡 —— 每个任务对应一个工作目录（目录去重）。
 *
 * 侧边栏展示的就是任务卡列表：在某个目录下新建任务时，
 * 若该目录已有任务卡则直接跳转，否则新建一张任务卡。
 */
data class Task(
    val id: String,
    val directory: String,   // 工作目录（agent 审计/操作的对象）
    val title: String,       // 任务标题（自动取自目录名或用户描述）
    val createdAt: Long,
    val lastOpened: Long,
) {

    fun toJson(): JSONObject = JSONObject().apply {
        put("id", id)
        put("directory", directory)
        put("title", title)
        put("createdAt", createdAt)
        put("lastOpened", lastOpened)
    }

    companion object {
        fun fromJson(o: JSONObject): Task = Task(
            id = o.optString("id", ""),
            directory = o.optString("directory", ""),
            title = o.optString("title", ""),
            createdAt = o.optLong("createdAt", 0L),
            lastOpened = o.optLong("lastOpened", 0L),
        )
    }
}
