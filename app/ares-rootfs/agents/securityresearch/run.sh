#!/bin/sh
# HOS-ARES 安全研究 Agent —— 联网检索 CVE / 漏洞情报 / 组件漏洞（open-websearch MCP）
#
# 设计原则：不做本地 CVE 库集成，改为实时联网搜索（多引擎、无需 API Key），
# 保证情报始终最新、避免把 CVE 数据打进本地 APK 资产。
#
# 用法: run.sh <target-path> [query-text]
export PATH=/usr/bin:/bin:/usr/sbin:/sbin:$PATH

echo "=========================================="
echo "  HOS ARES Security Research"
echo "=========================================="

TARGET="${1:-}"
QUERY="${2:-}"
[ -z "$QUERY" ] && QUERY="$TARGET"

# 1. 确保 node + open-websearch 可用（bootstrap 已尽力预装，此处兜底）
if ! command -v node >/dev/null 2>&1; then
    echo "[secresearch] 未找到 node，尝试安装 nodejs + npm ..."
    apk add --no-cache nodejs npm 2>&1 || {
        echo "[secresearch] 安装 node 失败（需联网），无法执行联网搜索。"
        exit 3
    }
fi
if ! command -v open-websearch >/dev/null 2>&1; then
    echo "[secresearch] 未找到 open-websearch，尝试安装 ..."
    npm install -g open-websearch 2>&1 || {
        echo "[secresearch] 安装 open-websearch 失败（需联网）。"
        exit 3
    }
fi

# 2. 注入搜索参数，交由 python 助手完成 daemon 管理 + 搜索 + 格式化
export HOS_SEARCH_QUERY="$QUERY"
export HOS_SEARCH_ENGINE="${HOS_SEARCH_ENGINE:-bing}"
export HOS_SEARCH_PORT="${HOS_SEARCH_PORT:-3210}"
export HOS_SEARCH_LIMIT="${HOS_SEARCH_LIMIT:-10}"
exec python3 "$(dirname "$0")/search.py"
