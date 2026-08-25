#!/usr/bin/env bash
# ============================================================
# HOS-ARES · L5 容器运行时 — Termux + proot-distro（Alpine 3.20）
# 目标：在 proot 隔离的 Alpine 容器内安装 reasonix + MCP 工具链
# 用法：bash setup-proot-alpine.sh    （在 Termux 内执行）
# 说明：安装后可用 proot-distro login alpine 进入容器
# ============================================================
set -euo pipefail

echo "==> [1/4] 安装 proot-distro 并部署 Alpine"
pkg install -y proot-distro
proot-distro install alpine

echo "==> [2/4] 容器内基础环境（Node.js 22+ / Python / Git）"
proot-distro login alpine -- sh -c '
  apk update
  apk add nodejs npm python3 py3-pip git bash
  node --version
'

echo "==> [3/4] 容器内安装 DeepSeek-Reasonix + MCP 工具链"
proot-distro login alpine -- sh -c '
  npm install -g reasonix
  reasonix --version
  pip3 install --break-system-packages -U mobile-security-mcp || pip3 install -U mobile-security-mcp
  npm install -g jadx-headless-mcp || true
'

echo "==> [4/4] 配置与验证"
proot-distro login alpine -- sh -c '
  mkdir -p ~/hos-ares
  if [ -f /data/data/com.termux/files/home/hos-ares-repo/config/reasonix.toml ]; then
    cp /data/data/com.termux/files/home/hos-ares-repo/config/reasonix.toml ~/hos-ares/reasonix.toml
  fi
  reasonix doctor --json | head -30
  reasonix mcp list || true
'

echo ""
echo "==> 完成。进入容器：proot-distro login alpine"
echo "==> 一键验证：proot-distro login alpine -- sh -c 'cd ~/hos-ares && reasonix -p \"hello\" --output-format stream-json'"