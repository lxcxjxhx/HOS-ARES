#!/bin/sh
# HOS-ARES rootfs 引导脚本（在 proot Alpine 内首次运行）。
# 功能:
#   1. 配置 apk 仓库
#   2. 安装 python3 + py3-pip（基础）
#   3. 离线检测：网络不可达时提示；若资产中有离线依赖包则优先解包
#   4. 依次安装各 agent 依赖（读取 /opt/agents-requirements/<name>.txt）
# 幂等：已安装的包 pip 会自动跳过；失败仅告警，不中断整体初始化。
export PATH=/usr/bin:/bin:/usr/sbin:/sbin:$PATH

# 需要安装依赖的 agent（顺序即安装顺序，与 requirements/ 清单一一对应）
AGENTS="argus deepaudit pentestgpt repoaudit strix securityresearch"

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
        echo "[bootstrap] 警告: apk 安装 python3 失败(需联网), 后续 agent 可能无法运行"
fi

# ---------- 离线检测 ----------
# 执行极短网络探测；busybox 有 wget 则优先用 wget --spider，
# 否则回退到 apk update 的成败作为网络是否可达的判断。
NET_OK=0
if command -v wget >/dev/null 2>&1; then
    if wget -q --spider --timeout=5 \
        https://dl-cdn.alpinelinux.org/alpine/v3.20/main/ 2>/dev/null; then
        NET_OK=1
    fi
else
    apk update >/dev/null 2>&1 && NET_OK=1
fi

if [ "$NET_OK" = "1" ]; then
    echo "[bootstrap] 网络可达，将在线安装 Python 依赖。"
else
    echo "[bootstrap] 网络不可达，无法在线安装 Python 依赖。"
fi

# ---------- 离线依赖包（可选，尽力而为）----------
# 若资产中存在预构建的离线依赖包目录(/opt/python-deps)，则解包到当前解释器的
# site-packages。Windows 上无法可靠预构建 musl-arm64 轮子，一般不会携带，
# 但保留该分支以便后续手工放入离线包时可直接使用。
if [ -d /opt/python-deps ]; then
    PY_SITE=""
    if command -v python3 >/dev/null 2>&1; then
        PY_SITE=$(python3 -c 'import site;print(site.getsitepackages()[0])' 2>/dev/null)
    fi
    if [ -n "$PY_SITE" ] && [ -n "$(ls -A /opt/python-deps 2>/dev/null)" ]; then
        echo "[bootstrap] 使用离线依赖包安装 (/opt/python-deps -> $PY_SITE)"
        if cp -a /opt/python-deps/. "$PY_SITE"/ 2>/dev/null; then
            echo "[bootstrap] 离线依赖包已解包完成。"
        else
            echo "[bootstrap] 警告: 离线依赖包解包失败, 请检查权限。"
        fi
    else
        echo "[bootstrap] 提示: 离线依赖包目录为空或无法确定 site-packages, 跳过。"
    fi
fi

# ---------- 安装各 agent 依赖 ----------
for name in $AGENTS; do
    reqfile="/opt/agents-requirements/${name}.txt"
    if [ ! -f "$reqfile" ]; then
        echo "[bootstrap] 跳过 $name: 未找到清单 $reqfile"
        continue
    fi
    echo "[bootstrap] 安装 $name 依赖..."
    # set +e: 单个 agent 依赖安装失败仅告警，不中断整体初始化。
    set +e
    if ! python3 -m pip install --no-cache-dir -r "$reqfile" 2>&1; then
        echo "[bootstrap] 警告: 安装 $name 依赖失败, 需联网; 该 agent 可能无法运行"
    fi
done

# ---------- 安全研究技能依赖（open-websearch MCP, 需要 Node 运行时）----------
# 联网检索 CVE/漏洞情报用；离线时跳过，运行时 run.sh 会再次兜底提示。
if [ "$NET_OK" = "1" ]; then
    echo "[bootstrap] 安装 nodejs + npm（安全研究技能）..."
    if ! command -v node >/dev/null 2>&1; then
        apk add --no-cache nodejs npm 2>&1 || \
            echo "[bootstrap] 警告: 安装 nodejs/npm 失败"
    fi
    if command -v npm >/dev/null 2>&1 && ! command -v open-websearch >/dev/null 2>&1; then
        echo "[bootstrap] 安装 open-websearch（npm 全局）..."
        npm install -g open-websearch 2>&1 || \
            echo "[bootstrap] 警告: 安装 open-websearch 失败(需联网)"
    fi
fi

echo "[bootstrap] 完成。"