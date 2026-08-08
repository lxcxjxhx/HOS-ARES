#!/bin/sh
# HOS-ARES 内置 FastAPI 后端 AI 审计平台（服务式）
# 需先在 rootfs 内启动后端，再通过其 REST API 触发扫描。
# LLM Key 由 rootfs 环境变量提供（配合 HOS 设置界面）。
# 用法: run.sh <目标路径> [任务文本]   # 启动后端（阻塞运行）
set -e
echo "=========================================="
echo "  HOS ARES Security Agent"
echo "=========================================="
TARGET="${1:-}"
TASK="${2:-}"

# 统一接入 llm_connect，注入统一的 LLM 连接配置（base url / 模型名 / 各 Key 别名）。
source /opt/agents/llm_connect.sh

# 从 rootfs 环境变量注入 LLM Key（配合 HOS 设置界面）。
export ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-}"
# 读取 GEMINI_API_KEY / CLAUDE_API_KEY，映射设置界面提供的键。
export GEMINI_API_KEY="${GEMINI_API_KEY:-$GOOGLE_API_KEY}"
export GOOGLE_API_KEY="${GOOGLE_API_KEY:-}"
export CLAUDE_API_KEY="${CLAUDE_API_KEY:-$ANTHROPIC_API_KEY}"

# 根据设置界面的后端（HOS_BACKEND）与模型（HOS_MODEL）设置通用配置。
if [ -n "${HOS_BACKEND:-}" ]; then
    export LLM_PROVIDER="${HOS_BACKEND}"
fi
if [ -n "${HOS_MODEL:-}" ]; then
    export LLM_MODEL="${HOS_MODEL}"
fi
if [ -n "${HOS_SERVER_URL:-}" ]; then
    export LLM_BASE_URL="${HOS_SERVER_URL}"
fi

if [ "$1" = "serve" ]; then
    echo "启动 HOS-ARES 审计后端: uvicorn app.main:app --host 127.0.0.1 --port 8000"
    cd /opt/agents/deepaudit
    exec python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
fi

echo "HOS-ARES 审计后端为服务式架构：LLM Key 已从 rootfs 环境变量注入。"
echo "请先启动后端："
echo "    run.sh serve"
echo "即  cd /opt/agents/deepaudit && uvicorn app.main:app --host 127.0.0.1 --port 8000"
echo "然后通过 http://127.0.0.1:8000 的 REST API 触发扫描。"
exit 2
