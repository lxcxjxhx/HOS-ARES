#!/bin/sh
# HOS-ARES rootfs 引导脚本（在 proot Alpine 内首次运行）
# 安装 python3 + pip 与最小依赖。需联网；失败时仅告警，不中断。
export PATH=/usr/bin:/bin:/usr/sbin:/sbin:$PATH

echo "[bootstrap] 配置 apk 仓库..."
if [ ! -s /etc/apk/repositories ]; then
    cat > /etc/apk/repositories <<'EOF'
https://dl-cdn.alpinelinux.org/alpine/v3.20/main
https://dl-cdn.alpinelinux.org/alpine/v3.20/community
EOF
fi

echo "[bootstrap] 安装 python3 + pip..."
if ! command -v python3 >/dev/null 2>&1; then
    apk add --no-cache python3 py3-pip 2>&1 || \
        echo "[bootstrap] 警告: apk 安装失败(需联网), 后续 agent 可能无法运行"
fi

echo "[bootstrap] 安装 argus 最小依赖(pyyaml)..."
python3 -m pip install --no-cache-dir pyyaml 2>&1 || \
    echo "[bootstrap] 警告: pip 安装失败(需联网)"

echo "[bootstrap] 完成。"
