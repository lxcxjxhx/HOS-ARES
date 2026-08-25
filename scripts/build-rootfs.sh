#!/usr/bin/env bash
# ============================================================
# HOS-ARES · Phase 5 打包 Job 1：烘烤 Alpine rootfs（自包含装载，APK 内嵌资产）
# 产出：build/rootfs.tar.xz（含 Node + reasonix + MCP 安全工具链）
# 用法：bash scripts/build-rootfs.sh   （需 Docker；CI 中用同一脚本）
# 修订记录：v3.0-ui 修正 CI 失败根因——
#   1) Alpine 官方源无 apktool/jadx 包（apk: no such package）→ 移除，改由
#      mobile-security-mcp 工具链（check_tools/analyze 等）完成 APK 静态分析闭环；
#   2) reasonix 版本锁定为本机实测通过的最低稳定版 1.19.1（npm 全局实测兼容），
#      避免臆测的 1.31.4 在 CI 装上后不可用。
# ============================================================
set -euo pipefail

OUT_DIR="${OUT_DIR:-build}"
ROOTFS_TAG="hos-ares-rootfs:$(date +%Y%m%d)"
REASONIX_VERSION="${REASONIX_VERSION:-1.19.1}"   # npm 全局实测稳定版（12 文兼容闭环）
MCP_VERSION="${MCP_VERSION:-1.28.1}"              # mobile-security-mcp 依赖锁定（12 文实测结论）

mkdir -p "${OUT_DIR}"

cat > "${OUT_DIR}/Dockerfile.rootfs" <<'EOF'
FROM alpine:3.20

# ── 基础（Node 运行时 + 常用 RE 系统工具；注明：Alpine 官方源无 apktool/jadx，故不列出）──
RUN apk add --no-cache nodejs npm python3 py3-pip git bash curl \
    radare2 binutils file zip unzip

# ── reasonix（统一 Agent 框架）──
RUN npm install -g reasonix@VERSION && reasonix --version

# ── mobile-security-mcp 工具链（隔离在 /opt/ares-libs，mcp SDK 锁 1.28.1）──
# 注意：原包 handler 为单参签名 call_tool(request)，mcp-SDK 各版本均以双参派发
#   → 直连必报 "takes 1 positional argument but 2 were given"（实测，见 12 文）。
#   解法：通过 mcp-compat-gw.py 适配层接入（绕过 SDK Server 装饰器，直调 handler），
#   本镜像已把网关脚本拷入 /root/hos-ares/tools/mcp-compat-gw.py。
RUN pip3 install --break-system-packages --no-cache-dir \
        --target /opt/ares-libs \
        "mcp==MCP_VERSION" "mobile-security-mcp" \
    && python3 -c "import sys; sys.path.insert(0,'/opt/ares-libs'); import mobile_security_mcp; print('mobile-security-mcp OK')"

# ── 运行时站点统一：reasonix 全局、MCP 库经 PYTHONPATH 注入 ──
ENV PYTHONPATH=/opt/ares-libs

# ── 项目工作区（serve 以 root 运行、配置预置）──
WORKDIR /root/hos-ares
COPY reasonix.toml /root/hos-ares/reasonix.toml
COPY config/reasonix.toml /etc/reasonix/config.toml
COPY tools/mcp-compat-gw.py /root/hos-ares/tools/mcp-compat-gw.py
# 网关运行所需 PYTHONPATH 显式指向 /opt/ares-libs（取代默认 tools/python-libs-compat 相对路径）
ENV HOS_ARES_PYTHONPATH=/opt/ares-libs

HEALTHCHECK NONE
EOF

# 替换版本占位符
sed -i "s/@VERSION/@${REASONIX_VERSION}/; s/MCP_VERSION/${MCP_VERSION}/" "${OUT_DIR}/Dockerfile.rootfs"

# 烘烤与归档（/ 目录瘦身后打 tar.xz）
docker build -t "${ROOTFS_TAG}" -f "${OUT_DIR}/Dockerfile.rootfs" .
docker run --rm "${ROOTFS_TAG}" sh -c "rm -rf /var/cache/apk/* /root/.npm/_cacache /tmp/*"
docker export "$(docker create "${ROOTFS_TAG}")" | xz -9 -T0 > "${OUT_DIR}/rootfs.tar.xz"

echo "==> 产物：${OUT_DIR}/rootfs.tar.xz（$(du -h "${OUT_DIR}/rootfs.tar.xz" | cut -f1)）"
echo "==> 后续：由 CI 注入 app/src/main/assets/rootfs.tar.xz，随 assembleRelease 打包"