#!/bin/sh
# Argus - 开源安全扫描器 (SAST/DAST/SCA/Secrets/IaC)，无需 AI
# 目标作为第 1 个参数传入；在 proot Alpine rootfs 内运行。
set -e
TARGET="$1"
if [ -z "$TARGET" ]; then
    echo "usage: run.sh <target-path>"; exit 2
fi
export PYTHONPATH=/opt/agents/argus/src
exec python3 -m argus.cli scan all "$TARGET" --format markdown
