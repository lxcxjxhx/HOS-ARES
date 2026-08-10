#!/bin/sh
# =============================================================================
# HOS-ARES 统一 LLM 连接脚本
# -----------------------------------------------------------------------------
# 该脚本供所有需要大模型（LLM）的技能插件统一 source 使用，用于把 Android
# 端注入的统一环境变量契约（HOS_LLM_BASE_URL / HOS_MODEL / DEEPSEEK_API_KEY）
# 归一化为各技能各自需要的环境变量别名，从而屏蔽不同技能间的 Key 命名差异。
#
# 用法（在技能 run.sh 中，于参数/usage 校验之后、导出/exec 之前调用）：
#     . /opt/agents/llm_connect.sh
# 或
#     source /opt/agents/llm_connect.sh
#
# 说明：本脚本仅做环境变量归一化，不退出、不阻塞，保证可被安全 source。
# =============================================================================

# 1) LLM 后端 base URL：读取 HOS_LLM_BASE_URL，缺省使用 DeepSeek 官方地址。
#    统一导出 LLM_BASE_URL，供 deepaudit 等读取 base url 的后端使用。
export LLM_BASE_URL="${HOS_LLM_BASE_URL:-https://api.deepseek.com}"

# 2) 模型名：读取 HOS_MODEL，缺省 deepseek-v4-flash。
#    同时归一化导出 LLM_MODEL（deepaudit 通用配置）与 STRIX_LLM（strix 模型名）。
export LLM_MODEL="${HOS_MODEL:-deepseek-v4-flash}"
export STRIX_LLM="${HOS_MODEL:-deepseek-v4-flash}"

# 3) 统一 DeepSeek API Key，派生各技能需要的 Key 别名。
#    仅在对应别名尚未设置（为空）时设置，避免覆盖用户在脚本里已显式配置的值。
if [ -n "${DEEPSEEK_API_KEY:-}" ]; then
    # repoaudit 的 LLM_utils 深度扫描使用 DEEPSEEK_API_KEY2
    [ -z "${DEEPSEEK_API_KEY2:-}" ] && export DEEPSEEK_API_KEY2="$DEEPSEEK_API_KEY"
    # strix 使用 OPENAI_API_KEY / LLM_API_KEY
    [ -z "${OPENAI_API_KEY:-}" ] && export OPENAI_API_KEY="$DEEPSEEK_API_KEY"
    [ -z "${LLM_API_KEY:-}" ] && export LLM_API_KEY="$DEEPSEEK_API_KEY"
    # pentestgpt 的 claude 后端使用 ANTHROPIC_API_KEY
    [ -z "${ANTHROPIC_API_KEY:-}" ] && export ANTHROPIC_API_KEY="$DEEPSEEK_API_KEY"
else
    # 4) 未配置统一 DeepSeek API Key：打印醒目中文提示（不退出），
    #    让底层工具自行报错，保证脚本可被安全 source。
    echo "[HOS] 未配置大模型 API Key，请到设置页粘贴 DeepSeek API Key 后重试"
fi
