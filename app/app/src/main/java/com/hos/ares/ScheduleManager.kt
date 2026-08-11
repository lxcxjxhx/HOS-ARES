package com.hos.ares

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter

/**
 * 定时任务调度（AlarmManager，无后台服务）：
 * 到点发**隐式**本地广播（action 匹配），MainActivity 动态注册的 receiver 接收并执行。
 * App 退出后调度不保留（手机端定时任务仅覆盖 App 存活期），重启后由 MainActivity 重挂。
 */
object ScheduleManager {

    const val ACTION = "com.hos.ares.SCHEDULED_TASK"
    const val EXTRA_TASK = "task"

    /** 注册/刷新定时任务（由 MainActivity.onResume 调用；关闭时取消）。 */
    fun refresh(context: Context, settings: SettingsStore) {
        val alarm = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        alarm.cancel(pending(context, ""))
        if (!settings.scheduleEnabled) return
        val task = settings.scheduleTask.trim()
        if (task.isEmpty()) return
        val intervalMs = settings.scheduleIntervalHours.coerceAtLeast(1) * 3600_000L
        alarm.setInexactRepeating(
            AlarmManager.RTC,
            System.currentTimeMillis() + intervalMs,
            intervalMs,
            pending(context, task),
        )
    }

    /** 隐式 action Intent（不带 setComponent，动态 receiver 才能收到），task 随 PendingIntent 更新。 */
    private fun pending(context: Context, task: String): PendingIntent {
        val intent = Intent(ACTION).putExtra(EXTRA_TASK, task)
        return PendingIntent.getBroadcast(
            context, 0, intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
    }

    fun filter(): IntentFilter = IntentFilter(ACTION)
}
