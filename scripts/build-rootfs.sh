#!/usr/bin/env bash
# ============================================================
# HOS-ARES · Phase 5 打包 Job 1：烘烤 Alpine rootfs（自包含装载，APK 内嵌资产）
# 产出：build/rootfs.tar.xz（含 Node + reasonix + MCP 安全工具链）
# 用法：bash scripts/build-rootfs.sh   （需 Docker；CI 中用同一脚本）
# 修订记录：
#   v3.0-ui     移除 apktool/jadx（Alpine 官方源无此包）→ CI 修复①
#   v3.0-bc     补 Rust/C 构建链 + --prefer-binary → CI 修复②
#   v3.0-rustup Alpine 3.20 自带 cargo 1.78 不支持 edition2024（mitmproxy-rs 需要）
#                → 改用 rustup 安装 stable（≥1.85），并保持 --prefer-binary → CI 修复③
# ============================================================
set -euo pipefail

OUT_DIR="${OUT_DIR:-build}"
ROOTFS_TAG="hos-ares-rootfs:$(date +%Y%m%d)"
REASONIX_VERSION="${REASONIX_VERSION:-1.19.1}"   # npm 全局实测稳定版（12 文兼容闭环）
MCP_VERSION="${MCP_VERSION:-1.28.1}"              # mcp SDK 锁 1.28.1（12 文实测结论）
MSEC_VERSION="${MSEC_VERSION:-0.1.4}"             # mobile-security-mcp 锁 0.1.4（12 文实测结论）

mkdir -p "${OUT_DIR}"

cat > "${OUT_DIR}/Dockerfile.rootfs" <<'EOF'
FROM alpine:3.20

# ── 基础：Node 运行时 + RE 系统工具 + C 构建链（musl 源码轮构建依赖）──
# 注：Alpine 官方源无 apktool/jadx（故不列出，APK 静态分析由 mobile-security-mcp 工具链闭环）。
RUN apk add --no-cache nodejs npm python3 py3-pip git bash curl \
    radare2 binutils file zip unzip \
    gcc g++ musl-dev python3-dev libffi-dev openssl-dev zlib-dev make

# ── Rust 工具链：Alpine 3.20 自带 cargo 1.78 不支持 edition2024（mitmproxy-rs）
#    → rustup stable（≥1.85）保证源码轮可编译 ──
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs -sSf | sh -s -- -y --profile minimal --default-toolchain stable \
 && /root/.cargo/bin/cargo --version
ENV PATH="/root/.cargo/bin:${PATH}" CARGO_HOME=/root/.cargo RUSTUP_HOME=/root/.rustup

# ── nightly musl 工具链：mitmproxy-linux-ebpf 的 aya-build 编译 eBPF 程序需要 ──
#   实测（第 6 轮日志）：error: toolchain 'nightly-x86_64-unknown-linux-musl' is not installed
#   → rustup 补装 nightly（profile minimal），cargo +nightly 可调用 → CI 修复④
RUN /root/.cargo/bin/rustup toolchain install nightly-x86_64-unknown-linux-musl --profile minimal \
 && /root/.cargo/bin/rustup component add rust-src --toolchain nightly-x86_64-unknown-linux-musl \
 && /root/.cargo/bin/cargo +nightly --version

# ── bpf-linker：mitmproxy-linux-ebpf build.rs 硬依赖（eBPF 重定向程序链接）──
#   实测：Apache build.rs 需 PATH 中可执行 bpf-linker（第 4 轮日志）；
#   但 cargo install 源码构建需 llvm-config（llvm-sys ≥231 匹配 LLVM≥23，Alpine 3.20 仅 LLVM17）
#   → 源码构建在 Alpine 3.20 必然失败（第 5 轮日志 exit 101）。
#   正确路线：官方预编译 musl 二进制（v0.11.0 tar.zst，零编译依赖，asset 已实测确认）。
RUN apk add --no-cache zstd libbpf-dev \
 && mkdir -p /tmp/bpf \
 && curl -sSL -o /tmp/bpf-linker.tar.zst \
      https://github.com/aya-rs/bpf-linker/releases/download/v0.11.0/bpf-linker-x86_64-unknown-linux-musl.tar.zst \
 && zstd -d -f /tmp/bpf-linker.tar.zst -o /tmp/bpf-linker.tar \
 && tar -xf /tmp/bpf-linker.tar -C /tmp/bpf \
 && install -m755 "$(find /tmp/bpf -name bpf-linker -type f | head -n1)" /usr/local/bin/bpf-linker \
 && /usr/local/bin/bpf-linker --version \
 && rm -rf /tmp/bpf /tmp/bpf-linker.tar /tmp/bpf-linker.tar.zst

# ── reasonix（统一 Agent 框架，npm 全局）──
RUN npm install -g reasonix@VERSION && reasonix --version

# ── mobile-security-mcp 工具链（隔离在 /opt/ares-libs，mcp SDK 锁 1.28.1）──
# 注意1：原包 handler 为单参签名 call_tool(request)，mcp-SDK 各版本均以双参派发
#   → 直连必报 "takes 1 positional argument but 2 were given"（实测，见 12 文）。
#   解法：通过 mcp-compat-gw.py 适配层接入（绕过 SDK Server 装饰器，直调 handler），
#   本镜像已把网关脚本拷入 /root/hos-ares/tools/mcp-compat-gw.py。
# 注意2：musl 下 frida/aioquic/mitmproxy-rs/mitmproxy-linux 无预编译 wheel → 源码构建
#   （已装 rustup-stable + C 链）；--prefer-binary 优先复用已有 wheel。
RUN pip3 install --break-system-packages --no-cache-dir --prefer-binary \
        --target /opt/ares-libs \
        "mcp==MCP_VERSION" "mobile-security-mcp==MSEC_VERSION" \
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

# 替换版本占位符（与 heredoc 文本精确对齐）
sed -i "s/@VERSION/@${REASONIX_VERSION}/; s/MCP_VERSION/${MCP_VERSION}/; s/MSEC_VERSION/${MSEC_VERSION}/" "${OUT_DIR}/Dockerfile.rootfs"

# 烘烤与归档（/ 目录瘦身后打 tar.xz）
docker build -t "${ROOTFS_TAG}" -f "${OUT_DIR}/Dockerfile.rootfs" .
# round-16：裁剪构建期工具链（运行时不需要编译工具）——.rustup/.cargo 共 ~4-5GB，
#   实测烘焙产物 rootfs.tar.xz 达 697.7MB（round-15 工件）→ 压缩阶段 OOM 的根因之一。
#   同时清理 npm/apk/doc 缓存，控制 APK 内嵌资产体积（11 文 §11.3 预算 Tier-A）。
docker run --rm "${ROOTFS_TAG}" sh -c "rm -rf /var/cache/apk/* /root/.cargo /root/.rustup /root/.npm /usr/share/doc /usr/share/man /tmp/*"
docker export "$(docker create "${ROOTFS_TAG}")" | xz -9 -T0 > "${OUT_DIR}/rootfs.tar.xz"

echo "==> 产物：${OUT_DIR}/rootfs.tar.xz（$(du -h "${OUT_DIR}/rootfs.tar.xz" | cut -f1)）"
echo "==> 后续：由 CI 注入 app/src/main/assets/rootfs.tar.xz，随 assembleRelease 打包"