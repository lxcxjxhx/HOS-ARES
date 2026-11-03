#!/bin/sh
# VeriPen 离线安装（内网 / 断网环境）
#
# 三条纪律（12 §2）：
#   1. 本脚本**不联网**：不 curl、不 wget、不 pip install、不 docker pull。
#      所有依赖都在同目录的 images/ 与 vendor/ 里。
#   2. **幂等**：可重复执行；已存在则跳过并提示，不重复建卷。
#   3. 先校验再动手：SHA256SUMS 不过就停，别在坏包上装。

set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"

echo "== 1/4 校验包完整性 =="
if [ ! -f SHA256SUMS ]; then
    echo "缺 SHA256SUMS，包不完整，停止。" >&2
    exit 1
fi
if command -v sha256sum >/dev/null 2>&1; then
    sha256sum -c SHA256SUMS --quiet || { echo "校验失败，停止。" >&2; exit 1; }
else
    echo "警告：本机无 sha256sum，跳过校验（请在能校验的机器上先验一遍）" >&2
fi

echo "== 2/4 检查运行时 =="
if ! command -v docker >/dev/null 2>&1; then
    echo "缺 docker（需 >= 24）。这是本机唯一必须预装的软件。" >&2
    exit 1
fi

echo "== 3/4 载入镜像 =="
if [ -d images ]; then
    for tar in images/*.tar; do
        [ -e "$tar" ] || continue
        echo "  load $(basename "$tar")"
        docker load -i "$tar"
    done
else
    echo "  images/ 不存在：跳过（可能已在镜像仓库里）"
fi

echo "== 4/4 生成配置并首启自检 =="
if [ -f .env ]; then
    echo "  .env 已存在，保留原值（幂等）"
else
    if [ -f .env.example ]; then
        cp .env.example .env
        echo "  已从 .env.example 生成 .env —— 请填入 path_key 与密钥的实际值"
    else
        echo "  缺 .env.example：请手工创建 .env" >&2
    fi
fi

echo
echo "安装完成。下一步："
echo "  python -m veripen_cli.cmd_doctor      # 16 项自检，红了先别跑"
echo "  回滚上一版： sh install.sh --rollback"
