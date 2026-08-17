#!/bin/sh
# HOS-ARES 完整 rootfs 预装构建脚本（在 Docker 内运行）
# 输出: /output/rootfs.tar (完整 Alpine + Python3 + 所有 pip 依赖)
set -e

OUTPUT_DIR="/output"
mkdir -p "$OUTPUT_DIR"
ROOTFS_SIZE_MB=0

echo "=== HOS-ARES 完整 rootfs 构建 ==="

# 1. 在当前 Alpine 中安装所有 Python 依赖
echo "[1] 安装 Python 依赖..."
pip install --no-cache-dir \
    pyyaml \
    argus-languages \
    openai \
    anthropic \
    tqdm \
    litellm \
    pydantic \
    pydantic-settings \
    requests \
    rich \
    pygments \
    jinja2 \
    reportlab \
    deepseek-reasonix \
    2>&1 | tail -5

echo "[2] Python 依赖安装完成，已安装包:"
pip list 2>/dev/null | wc -l | xargs -I{} echo "  {} 个包"

# 2. 记录 Python 路径
PYTHON_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "[3] Python 版本: $PYTHON_VER"

# 3. 创建 Agent 运行时目录结构
echo "[4] 创建 Agent 运行时目录..."
mkdir -p /opt/agents
mkdir -p /opt/agents-requirements
mkdir -p /opt/skills
mkdir -p /opt/python-deps

# 4. 创建基础 shim 脚本
echo "[5] 创建 shim 脚本..."

# reasonix shim
cat > /usr/local/bin/reasonix << 'SHIM'
#!/bin/sh
PYTHONPATH=/opt/agents/reasonix exec python3 /opt/agents/reasonix/reasonix_agent.py "$@"
SHIM
chmod +x /usr/local/bin/reasonix

# argus shim
cat > /usr/local/bin/argus << 'SHIM'
#!/bin/sh
PYTHONPATH=/opt/agents/argus/src exec python3 -m argus.cli "$@"
SHIM
chmod +x /usr/local/bin/argus

# repoaudit shim
cat > /usr/local/bin/repoaudit << 'SHIM'
#!/bin/sh
PYTHONPATH=/opt/agents/repoaudit/src:$PYTHONPATH exec python3 /opt/agents/repoaudit/src/repoaudit.py "$@"
SHIM
chmod +x /usr/local/bin/repoaudit

# strix shim
cat > /usr/local/bin/strix << 'SHIM'
#!/bin/sh
PYTHONPATH=/opt/agents/strix exec python3 -m strix "$@"
SHIM
chmod +x /usr/local/bin/strix

# 5. 清理缓存
echo "[6] 清理缓存..."
pip cache purge 2>/dev/null || true
find /usr/lib/python* -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find /usr/lib/python* -name "*.pyc" -delete 2>/dev/null || true
rm -rf /tmp/* /var/cache/apk/*

# 6. 创建版本标记
echo "[7] 创建版本标记..."
cat > /opt/HOSARES_VERSION << 'VERSION'
HOS-ARES rootfs
python3: pre-installed
deps: pre-installed
build: offline
VERSION

# 7. 打包为 tar
echo "[8] 打包 rootfs..."
cd /
tar cf "$OUTPUT_DIR/rootfs.tar" \
    --exclude='.dockerenv' \
    --exclude='proc/*' \
    --exclude='sys/*' \
    --exclude='dev/*' \
    --exclude='tmp/*' \
    --exclude='run/*' \
    bin/ \
    etc/ \
    lib/ \
    libexec/ \
    sbin/ \
    usr/ \
    var/ \
    opt/ \
    root/ \
    home/

# 8. 压缩
echo "[9] 压缩 rootfs.tar..."
# 不压缩，保持 .tar 格式，Android 端解压更快

ROOTFS_SIZE=$(du -h "$OUTPUT_DIR/rootfs.tar" | cut -f1)
echo "[10] rootfs.tar 大小: $ROOTFS_SIZE"

echo "=== 构建完成 ==="
echo "输出文件: $OUTPUT_DIR/rootfs.tar"