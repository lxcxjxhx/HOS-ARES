package com.hos.ares.gateway

/**
 * SkillRegistry：关键词计分路由。
 * 命中规则：输入文本按 keywords 逐个匹配（忽略大小写），计分最高的 Skill 命中；
 * 同分取列表先序；无命中回落到 [BuiltinSkills.fallback]（chat）。
 */
class SkillRegistry(private val skills: List<Skill> = BuiltinSkills.all) {

    fun classify(input: String): Skill {
        val normalized = input.lowercase()
        var best: Skill? = null
        var bestScore = 0
        for (skill in skills) {
            var score = 0
            for (kw in skill.keywords) {
                if (normalized.contains(kw.lowercase())) score++
            }
            if (score > bestScore) {
                bestScore = score
                best = skill
            }
        }
        return best ?: BuiltinSkills.fallback
    }
}