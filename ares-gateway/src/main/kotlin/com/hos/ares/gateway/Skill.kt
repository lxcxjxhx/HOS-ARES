package com.hos.ares.gateway

/** 安全能力 Skill 描述：路由目标 + 执行模型 + 关联 MCP 工具链 */
data class Skill(
    val id: String,
    val name: String,
    val description: String,
    val keywords: List<String>,
    val model: String = "deepseek-v4-flash",
    /** 常驻服务通道（serve）会话标识，用于前缀缓存复用 */
    val session: String = id,
    val mcpServers: List<String> = emptyList(),
)

object BuiltinSkills {
    val all: List<Skill> = listOf(
        Skill(
            id = "apk-static", name = "APK 静态分析",
            description = "APK 反编译、dex/资源分析、密钥扫描（apktool/jadx/apkid）",
            keywords = listOf("apk", "反编译", "静态分析", "逆向", "dex", "smali", "apkid", "jadx"),
            mcpServers = listOf("mobile-security"),
        ),
        Skill(
            id = "dynamic-hook", name = "动态插桩",
            description = "Frida spawn/attach/inject、Objection，运行时 hook 与追踪",
            keywords = listOf("frida", "插桩", "hook", "运行时", "动态分析", "objection"),
            mcpServers = listOf("mobile-security"),
        ),
        Skill(
            id = "rasp-bypass", name = "RASP / 加固绕过",
            description = "Zimperium/DexGuard/Promon/Arxan 识别与绕过分析",
            keywords = listOf("rasp", "dexguard", "promon", "arxan", "zimperium", "加固"),
            mcpServers = listOf("mobile-security"),
        ),
        Skill(
            id = "sca-audit", name = "依赖漏洞审计 (SCA)",
            description = "第三方组件/依赖漏洞扫描（CVEs、许可证分析）",
            keywords = listOf("sca", "依赖", "cve", "组件", "漏洞扫描", "gradle", "pom"),
            mcpServers = listOf("mobile-security", "mcp-termux"),
        ),
        Skill(
            id = "pentest", name = "移动渗透测试",
            description = "渗透测试、流量分析、内存分析（stackplz/paradise/radare2）",
            keywords = listOf("渗透", "漏洞利用", "越权", "流量", "抓包", "内存分析", "eBPF"),
            mcpServers = listOf("mcp-termux", "mobile-security"),
        ),
        Skill(
            id = "chat", name = "通用问答（兜底）",
            description = "未匹配 Skill 时的通用对话/代码问答",
            keywords = emptyList(),
        ),
    )

    val fallback: Skill get() = all.first { it.id == "chat" }
}