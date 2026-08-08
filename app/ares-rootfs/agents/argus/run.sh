#!/bin/sh
# HOS-ARES 内置安全 Agent（SAST/DAST/SCA/Secrets/IaC 扫描，无需 AI）
# 目标作为第 1 个参数传入；任务文本作为第 2 个参数（可选，用于记录）。
# 在 proot Alpine rootfs 内运行。
set -e
echo "=========================================="
echo "  HOS ARES Security Agent"
echo "=========================================="
TARGET="$1"
TASK="${2:-}"
if [ -z "$TARGET" ]; then
    echo "usage: run.sh <target-path> [task-text]"; exit 2
fi
export PYTHONPATH=/opt/agents/argus/src
exec python3 -m argus.cli scan all "$TARGET" --format markdown
