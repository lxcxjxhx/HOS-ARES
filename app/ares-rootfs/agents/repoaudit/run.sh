#!/bin/sh
# RepoAudit - 基于 LLM 的符号执行 + 神经代码审计
# 需要 LLM Key（由 rootfs 内环境变量提供，可在设置界面配置）。
# 用法: run.sh <target-path> [language] [scan-type]
#   scan-type: metascan(默认，无需 LLM Key) | dfbscan(需 --model-name 与 LLM Key)
set -e
TARGET="$1"
LANG="${2:-Python}"
SCAN_TYPE="${3:-metascan}"
if [ -z "$TARGET" ]; then
    echo "usage: run.sh <target-path> [language] [scan-type]"; exit 2
fi

# 从 rootfs 环境变量注入 LLM Key（配合 HOS 设置界面）。
# RepoAudit 的 LLM_utils 按模型名读取：gpt/o3 -> OPENAI_API_KEY，
# claude -> ANTHROPIC_API_KEY，gemini -> GOOGLE_API_KEY，deepseek -> DEEPSEEK_API_KEY2。
export ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-}"
export GOOGLE_API_KEY="${GOOGLE_API_KEY:-}"
export DEEPSEEK_API_KEY2="${DEEPSEEK_API_KEY2:-$OPENAI_API_KEY}"

export PYTHONPATH=/opt/agents/repoaudit/src
# 若设置界面指定了模型名（HOS_MODEL），则传给 RepoAudit（dfbscan 使用）。
if [ -n "${HOS_MODEL:-}" ]; then
    exec python3 /opt/agents/repoaudit/src/repoaudit.py \
        --scan-type "$SCAN_TYPE" \
        --project-path "$TARGET" \
        --language "$LANG" \
        --model-name "$HOS_MODEL"
fi
exec python3 /opt/agents/repoaudit/src/repoaudit.py \
    --scan-type "$SCAN_TYPE" \
    --project-path "$TARGET" \
    --language "$LANG"
