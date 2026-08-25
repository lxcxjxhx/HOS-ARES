#!/usr/bin/env bash
# ============================================================
# HOS-ARES · Phase 1/2 部署脚本 — Android Termux
# 目标：在 Termux 内安装 reasonix + MCP 安全工具链，并写入项目配置
# 依据实测 v1.19.1 命令面（见 方案/.../07-实测偏差与修正.md）
# 用法：bash setup-termux.sh   （在 Termux 内执行）
# ============================================================
set -euo pipefail

echo "==> [1/6] Termux 基础环境"
pkg update -y && pkg upgrade -y
pkg install -y nodejs-lts python git openssh \
  apktool jadx frida-tools radare2 binutils || \
  pkg install -y nodejs-lts python git

echo "==> [2/6] 安装 DeepSeek-Reasonix（统一 Agent 框架）"
npm i -g reasonix
reasonix --version

echo "==> [3/6] 写入项目配置 reasonix.toml"
PROJECT_DIR="${HOME}/hos-ares"
mkdir -p "${PROJECT_DIR}"
REPO_TOML="$(cd "$(dirname "$0")/.." && pwd)/config/reasonix.toml"
if [ -f "${REPO_TOML}" ]; then
  cp "${REPO_TOML}" "${PROJECT_DIR}/reasonix.toml"
  echo "    已复制仓库 config/reasonix.toml -> ${PROJECT_DIR}/reasonix.toml"
else
  echo "    未找到仓库 config/reasonix.toml，跳过（可稍后手动放置）"
fi

echo "==> [4/6] 配置 DeepSeek API Key"
if [ -z "${DEEPSEEK_API_KEY:-}" ]; then
  echo "    提示：请设置环境变量 DEEPSEEK_API_KEY（或运行 reasonix setup 向导）"
  echo "    echo 'export DEEPSEEK_API_KEY=sk-xxxx' >> ~/.bashrc"
else
  echo "    已检测到 DEEPSEEK_API_KEY"
fi

echo "==> [5/6] 安装 MCP 安全工具链（L4）"
pip install -U mobile-security-mcp 2>/dev/null || pip install -U pip && pip install -U mobile-security-mcp
npm i -g jadx-headless-mcp 2>/dev/null || echo "    jadx-headless-mcp 安装失败（可跳过）"
# mcp-termux v7.0 提供 73 个 RE 工具（stackplz / paradise / radare2 …）
# git clone https://github.com/xxx/mcp-termux && cd mcp-termux && npm i && npm link
echo "    提示：mcp-termux 请按上游仓库说明构建（Rust/ARM64 Root 特性）"

echo "==> [6/6] 验证"
reasonix doctor --json | head -40 || true
echo "--- MCP 注册检查 ---"
(reasonix mcp list 2>&1 || true) | head -20
echo ""
echo "==> 完成。快速验证：reasonix -p \"hello\" --model deepseek-v4-flash --output-format stream-json"
echo "==> 服务模式（AresGateway 首选通道）：reasonix serve --addr 127.0.0.1:8931 --auth token"