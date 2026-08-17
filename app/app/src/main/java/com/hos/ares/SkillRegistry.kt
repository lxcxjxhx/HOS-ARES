package com.hos.ares

/**
 * HOS-ARES 安全技能注册表（原创实现）
 *
 * 对应 PLAN 中的 Security Skill Layer：每个安全 Agent 是一个"技能插件"，
 * 声明了触发关键词与参与的工作流。Ares 统一入口据此做任务识别与调度。
 */
object SkillRegistry {

    data class Skill(
        val name: String,          // 技能名 = agent 名
        val triggers: List<String>,// 触发关键词
        val requiresLl: Boolean,   // 是否需要 LLM
    )

    val skills: List<Skill> = listOf(
        Skill("argus", listOf("漏洞", "扫描", "vulnerability", "scan", "sast", "sca", "secrets"), requiresLl = false),
        Skill("repoaudit", listOf("审计", "符号执行", "audit", "code review", "代码审计", "空指针", "uaf", "内存泄漏"), requiresLl = true),
        Skill("strix", listOf("渗透", "pentest", "攻击", "exploit", "渗透测试", "漏洞利用", "ctf"), requiresLl = true),
        // 统一 Agent 入口：触发词宽松，作为 reasonix 统一入口的候选识别
        Skill(
            "reasonix",
            listOf("审计", "audit", "漏洞", "vulnerability", "渗透", "pentest", "深度", "deep", "scan", "代码审查"),
            requiresLl = true,
        ),
    )

    /** 根据用户任务文本做简单关键词识别，返回可能相关的技能。 */
    fun recognize(task: String): List<Skill> {
        val lower = task.lowercase()
        return skills.filter { s ->
            s.triggers.any { t -> lower.contains(t.lowercase()) }
        }.ifEmpty { skills.take(1) }
    }
}
