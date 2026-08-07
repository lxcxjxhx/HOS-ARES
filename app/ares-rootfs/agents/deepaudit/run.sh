#!/bin/sh
# DeepAudit - FastAPI 后端 AI 审计平台（服务式）
# 需先在 rootfs 内启动后端，再通过其 REST API 触发扫描。
# LLM Key 由 rootfs 环境变量提供（配合 HOS 设置界面）。
# 用法: run.sh serve   # 启动后端（阻塞运行）
#        run.sh <任意>  # 打印启动指引
set -e

# 从 rootfs 环境变量注入 LLM Key（配合 HOS 设置界面）。
export ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-}"
# DeepAudit 读取 GEMINI_API_KEY / CLAUDE_API_KEY，映射设置界面提供的键。
export GEMINI_API_KEY="${GEMINI_API_KEY:-$GOOGLE_API_KEY}"
export GOOGLE_API_KEY="${GOOGLE_API_KEY:-}"
export CLAUDE_API_KEY="${CLAUDE_API_KEY:-$ANTHROPIC_API_KEY}"

# 根据设置界面的后端（HOS_BACKEND）与模型（HOS_MODEL）设置 DeepAudit 通用配置。
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
    echo "启动 DeepAudit 后端: uvicorn app.main:app --host 127.0.0.1 --port 8000"
    cd /opt/agents/deepaudit
    exec python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
fi

echo "DeepAudit 为服务式架构：LLM Key 已从 rootfs 环境变量注入。"
echo "请先启动后端："
echo "    run.sh serve"
echo "即  cd /opt/agents/deepaudit && uvicorn app.main:app --host 127.0.0.1 --port 8000"
echo "然后通过 http://127.0.0.1:8000 的 REST API 触发扫描。"
exit 2
