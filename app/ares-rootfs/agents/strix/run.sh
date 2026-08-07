#!/bin/sh
# Strix - 开源 AI 渗透测试 Agent
# 需要 LLM Key（由 rootfs 环境变量提供，Strix 经 pydantic-settings 读取
# OPENAI_API_KEY / LLM_API_KEY，模型名读取 STRIX_LLM）。
# 用法: run.sh <target> [scan-mode]
set -e
TARGET="$1"
MODE="${2:-quick}"
if [ -z "$TARGET" ]; then
    echo "usage: run.sh <target> [scan-mode]"; exit 2
fi

# 从 rootfs 环境变量注入 LLM Key（配合 HOS 设置界面）。
export OPENAI_API_KEY="${OPENAI_API_KEY:-}"
export LLM_API_KEY="${LLM_API_KEY:-$OPENAI_API_KEY}"

# 若设置界面指定了模型名（HOS_MODEL），映射为 Strix 的 STRIX_LLM。
if [ -n "${HOS_MODEL:-}" ]; then
    export STRIX_LLM="${HOS_MODEL}"
fi

export PYTHONPATH=/opt/agents/strix
exec python3 -m strix -n -t "$TARGET" --scan-mode "$MODE" --max-budget 10
