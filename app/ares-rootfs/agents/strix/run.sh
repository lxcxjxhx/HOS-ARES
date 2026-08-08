#!/bin/sh
# HOS-ARES 内置 AI 渗透测试 Agent
# 需要 LLM Key（由 rootfs 环境变量提供，经 pydantic-settings 读取
# OPENAI_API_KEY / LLM_API_KEY，模型名读取 STRIX_LLM）。
# 用法: run.sh <target-path> [task-text]
set -e
echo "=========================================="
echo "  HOS ARES Security Agent"
echo "=========================================="
TARGET="$1"
TASK="${2:-}"
MODE="quick"
if [ -z "$TARGET" ]; then
    echo "usage: run.sh <target-path> [task-text]"; exit 2
fi

# 统一接入 llm_connect，注入统一的 LLM 连接配置（base url / 模型名 / 各 Key 别名）。
source /opt/agents/llm_connect.sh

# 从 rootfs 环境变量注入 LLM Key（配合 HOS 设置界面）。
export OPENAI_API_KEY="${OPENAI_API_KEY:-}"
export LLM_API_KEY="${LLM_API_KEY:-$OPENAI_API_KEY}"

# 默认禁用上游遥测，避免回传数据（可通过 STRIX_TELEMETRY 显式开启）。
export STRIX_TELEMETRY="${STRIX_TELEMETRY:-0}"

# 若设置界面指定了模型名（HOS_MODEL），映射为 STRIX_LLM。
if [ -n "${HOS_MODEL:-}" ]; then
    export STRIX_LLM="${HOS_MODEL}"
fi

export PYTHONPATH=/opt/agents/strix
exec python3 -m strix -n -t "$TARGET" --scan-mode "$MODE" --max-budget 10
