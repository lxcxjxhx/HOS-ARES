#!/bin/sh
# =============================================================================
# HOS-ARES reasonix 统一 Agent 入口启动脚本
# -----------------------------------------------------------------------------
# 作为所有安全技能的"统一调度入口"，先注入统一的 LLM 连接配置，
# 再交由 reasonix_agent.py 完成任务类型识别与技能派发。
#
# 用法: run.sh <target-path> [task-text]
#   在 proot Alpine rootfs 内以如下方式执行：
#     /bin/sh /opt/agents/reasonix/run.sh /work "<task>"
# =============================================================================
set -e

# 用法校验：缺少目标路径时打印 usage 并退出（退出码 2）。
TARGET="$1"
TASK="${2:-}"
if [ -z "$TARGET" ]; then
    echo "usage: run.sh <target-path> [task-text]"
    exit 2
fi

# 注入统一的 LLM 连接配置（base url / 模型名 / 各 Key 别名）。
# 需在导出 PYTHONPATH 与 exec 之前完成，保证 Python 调度脚本能看到全部变量。
source /opt/agents/llm_connect.sh

# 将 reasonix 源码目录加入 Python 模块搜索路径。
export PYTHONPATH=/opt/agents/reasonix

# 执行 reasonix 统一调度脚本（内部完成技能识别与派发）。
exec python3 /opt/agents/reasonix/reasonix_agent.py "$TARGET" "$TASK"
