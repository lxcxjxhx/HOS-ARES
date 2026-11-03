#!/bin/sh
# VeriPen 卸载（**保留数据**：删容器不删卷）
#
# 12 §2 的要求：卸载脚本同样不联网，"保留数据，删容器"。

set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"

echo "== 停止并删除容器（保留卷与数据）=="
if [ -f docker-compose.yml ]; then
    docker compose -f docker-compose.yml down --rmi local
else
    echo "  未找到 docker-compose.yml，跳过容器清理" >&2
fi

echo
echo "已停止。数据卷与 out/ 未删除——重装后可直接接续。"
echo "确需彻底清除时，由人工执行 docker volume rm，本脚本不代劳。"
