#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# HOS-ARES reasonix 统一 Agent 入口（Python 调度脚本）
# -----------------------------------------------------------------------------
# 职责：
#   1. 从命令行参数与统一环境变量契约读取目标路径、任务文本与 LLM 配置；
#   2. 根据任务文本关键词识别需要调度的技能（镜像 Android 端 SkillRegistry.kt）；
#   3. 对每个命中的技能，以子进程方式执行对应 run.sh，并捕获其输出；
#   4. 打印统一格式的结构化事件标记（HOS-SKILL:...），供 Android 网关解析
#      更新 agent 卡；
#   5. 汇总成功/失败数量并给出进程退出码。
#
# 实现约束：仅使用 Python 标准库，不引入任何外部依赖，保证离线稳定可用。
# =============================================================================

import os
import sys
import subprocess

# -----------------------------------------------------------------------------
# 统一环境变量契约
# -----------------------------------------------------------------------------
# 后端名（HOS_BACKEND），缺省 deepseek
BACKEND = os.environ.get("HOS_BACKEND", "deepseek")
# 模型名（HOS_MODEL），缺省 deepseek-v4-flash
MODEL = os.environ.get("HOS_MODEL", "deepseek-v4-flash")
# 统一 DeepSeek API Key（DEEPSEEK_API_KEY）
DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "")

# 每个技能的派发超时（秒），此处为 15 分钟。
SKILL_TIMEOUT = int(os.environ.get("HOS_SKILL_TIMEOUT", "900"))

# -----------------------------------------------------------------------------
# 技能定义
# -----------------------------------------------------------------------------
# 每个技能：run.sh 路径、是否需要 LLM、触发关键词（小写包含匹配）。
# 关键词与 Android 端 SkillRegistry.kt 保持一致。
SKILLS = [
    {
        "name": "argus",
        "needs_llm": False,
        "keywords": ["漏洞", "扫描", "vulnerability", "scan"],
    },
    {
        "name": "repoaudit",
        "needs_llm": True,
        "keywords": ["审计", "符号执行", "audit", "code review"],
    },
    {
        "name": "strix",
        "needs_llm": True,
        "keywords": ["渗透", "pentest", "攻击", "exploit"],
    },
    {
        "name": "pentestgpt",
        "needs_llm": True,
        "keywords": ["渗透测试", "pentestgpt"],
    },
    {
        "name": "deepaudit",
        "needs_llm": True,
        "keywords": ["深度", "deep", "后端"],
    },
    {
        "name": "securityresearch",
        "needs_llm": False,
        "keywords": ["cve", "漏洞情报", "威胁情报", "情报", "search", "research"],
    },
]

# 未命中任何关键词时的默认派发技能。
DEFAULT_SKILLS = ["argus"]


def match_skills(task: str) -> list:
    """根据任务文本关键词，返回需要派发的技能名列表（保持定义顺序）。"""
    if not task:
        return list(DEFAULT_SKILLS)
    lowered = task.lower()
    matched = []
    for skill in SKILLS:
        for kw in skill["keywords"]:
            if kw in lowered:
                matched.append(skill["name"])
                break
    if not matched:
        matched = list(DEFAULT_SKILLS)
    return matched


def run_skill(skill_name: str, target: str, task: str) -> bool:
    """派发单个技能并返回是否成功。返回 False 表示失败/异常/超时，但绝不阻断整体。"""
    # 需要 LLM 但未配置统一 API Key：打印中文引导并把该技能标记为 FAILED（不 crash）。
    skill = next((s for s in SKILLS if s["name"] == skill_name), None)
    needs_llm = skill["needs_llm"] if skill else True
    if needs_llm and not DEEPSEEK_KEY:
        print("[HOS] 未配置大模型 API Key，请到设置页粘贴 DeepSeek API Key 后重试")
        print(f"HOS-SKILL:{skill_name}:FAILED")
        return False

    # 技能运行前打印 RUNNING 事件标记。
    print(f"HOS-SKILL:{skill_name}:RUNNING")

    script = f"/opt/agents/{skill_name}/run.sh"
    try:
        proc = subprocess.run(
            ["sh", script, target, task],
            capture_output=True,
            text=True,
            timeout=SKILL_TIMEOUT,
        )
        # 打印该技能的输出（分隔标题 + stdout/stderr）。
        print(f"===== [{skill_name}] =====")
        if proc.stdout:
            print(proc.stdout.rstrip())
        if proc.stderr:
            print(proc.stderr.rstrip())
        if proc.returncode == 0:
            print(f"HOS-SKILL:{skill_name}:DONE")
            return True
        print(f"HOS-SKILL:{skill_name}:FAILED")
        return False
    except FileNotFoundError:
        print(f"===== [{skill_name}] =====")
        print(f"[reasonix] 未找到技能脚本 {script}，跳过该技能。")
        print(f"HOS-SKILL:{skill_name}:FAILED")
        return False
    except subprocess.TimeoutExpired:
        print(f"===== [{skill_name}] =====")
        print(f"[reasonix] 技能 {skill_name} 执行超时（{SKILL_TIMEOUT} 秒），已终止。")
        print(f"HOS-SKILL:{skill_name}:FAILED")
        return False
    except Exception as exc:  # 任何异常都只标记失败，不阻断整体。
        print(f"===== [{skill_name}] =====")
        print(f"[reasonix] 技能 {skill_name} 执行异常：{exc}")
        print(f"HOS-SKILL:{skill_name}:FAILED")
        return False


def main() -> None:
    # 从 sys.argv 读取目标路径与任务文本。
    if len(sys.argv) < 2 or not sys.argv[1]:
        print("usage: reasonix_agent.py <target-path> [task-text]")
        sys.exit(2)
    target = sys.argv[1]
    task = sys.argv[2] if len(sys.argv) > 2 else ""

    # 打印运行头信息（便于排查）。
    print("==========================================")
    print("  HOS ARES Reasonix Unified Agent")
    print("==========================================")
    print(f"[reasonix] backend={BACKEND} model={MODEL}")
    print(f"[reasonix] target={target} task={task}")

    # 任务类型识别：命中哪些技能。
    skills = match_skills(task)
    print(f"[reasonix] 命中的技能: {', '.join(skills)}")

    # 逐个派发技能（失败/缺失不阻断整体）。
    success = 0
    failed = 0
    for skill_name in skills:
        if run_skill(skill_name, target, task):
            success += 1
        else:
            failed += 1

    # 最后打印统一汇总。
    print("==========================================")
    print(f"[reasonix] 汇总：成功 {success} 个，失败 {failed} 个。")
    print("==========================================")

    # 若至少一个技能成功则退出 0，否则退出 1。
    if success > 0:
        sys.exit(0)
    sys.exit(1)


if __name__ == "__main__":
    main()
