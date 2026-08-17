#!/bin/sh
# HOS-ARES rootfs 引导脚本（预装模式）
#
# 两种模式：
#   1. 预装模式 (rootfs 已含 Python3 + 所有依赖): 仅做验证，无需联网
#   2. 在线模式 (rootfs 只有 minirootfs): 联网安装 Python + 依赖（fallback）
#
# 判定方式: /opt/HOSARES_PREINSTALLED 文件存在则为预装模式
export PATH=/usr/bin:/bin:/usr/sbin:/sbin:$PATH

PREINSTALLED="/opt/HOSARES_PREINSTALLED"

echo "[bootstrap] HOS-ARES 引导脚本启动..."

if [ -f "$PREINSTALLED" ]; then
    echo "[bootstrap] 检测到预装标记 /opt/HOSARES_PREINSTALLED"
    echo "[bootstrap] 预装模式: 跳过在线安装，仅验证环境..."

    # 验证 python3
    if command -v python3 >/dev/null 2>&1; then
        PY_VER=$(python3 --version 2>&1)
        echo "[bootstrap] ✓ python3 可用: $PY_VER"
    else
        echo "[bootstrap] ✗ python3 不可用! 预装 rootfs 可能损坏。"
        echo "[bootstrap]   建议: 清除应用数据后重新安装。"
        exit 1
    fi

    # 验证核心依赖
    echo "[bootstrap] 验证核心依赖..."
    MISSING=0
    for mod in yaml argus_languages openai anthropic tqdm litellm rich jinja2 reportlab deepseek_reasonix; do
        # 用 python3 -c 检测每个模块
        MOD_NAME=$(echo "$mod" | tr '_' '.')
        if python3 -c "import $mod" 2>/dev/null; then
            echo "[bootstrap]   ✓ $mod"
        else
            # 有些包名和 pip 名不同
            case "$mod" in
                deepseek_reasonix)
                    if python3 -c "import reasonix" 2>/dev/null; then
                        echo "[bootstrap]   ✓ reasonix"
                    else
                        echo "[bootstrap]   ✗ $mod (未安装)"
                        MISSING=$((MISSING + 1))
                    fi
                    ;;
                *)
                    echo "[bootstrap]   ✗ $mod (未安装)"
                    MISSING=$((MISSING + 1))
                    ;;
            esac
        fi
    done

    if [ "$MISSING" -gt 0 ]; then
        echo "[bootstrap] ⚠ 有 $MISSING 个依赖缺失，可能影响部分 Agent 功能。"
        echo "[bootstrap]   尝试从 wheel 备份目录补装..."
        if [ -d /opt/python-deps ]; then
            PY_SITE=$(python3 -c 'import site;print(site.getsitepackages()[0])' 2>/dev/null)
            if [ -n "$PY_SITE" ]; then
                # 解压 wheel 文件 (wheel = zip)
                for whl in /opt/python-deps/*.whl; do
                    if [ -f "$whl" ]; then
                        echo "[bootstrap]   补装: $(basename "$whl")"
                        python3 -c "
import zipfile, sys
with zipfile.ZipFile('$whl') as z:
    for f in z.namelist():
        if '.data/' not in f and not f.endswith('.pyc') and '__pycache__' not in f:
            z.extract(f, '$PY_SITE')
" 2>/dev/null
                    fi
                done
            fi
        fi
    fi

    # 验证 Agent 源码
    echo "[bootstrap] 验证 Agent 源码..."
    for agent in argus repoaudit strix reasonix; do
        if [ -d "/opt/agents/$agent" ]; then
            echo "[bootstrap]   ✓ $agent"
        else
            echo "[bootstrap]   ✗ $agent 源码缺失"
        fi
    done

    echo "[bootstrap] 预装模式验证完成。"
    exit 0
fi

# ========== 在线模式 (fallback) ==========
echo "[bootstrap] 预装标记不存在，进入在线模式..."

AGENTS="argus repoaudit strix reasonix"

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
        echo "[bootstrap] 警告: apk 安装 python3 失败(需联网)"
fi

# 离线检测
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
    echo "[bootstrap] 网络可达，在线安装 Python 依赖..."
else
    echo "[bootstrap] 网络不可达，尝试离线依赖..."
fi

# 离线依赖包
if [ -d /opt/python-deps ]; then
    PY_SITE=""
    if command -v python3 >/dev/null 2>&1; then
        PY_SITE=$(python3 -c 'import site;print(site.getsitepackages()[0])' 2>/dev/null)
    fi
    if [ -n "$PY_SITE" ] && [ -n "$(ls -A /opt/python-deps 2>/dev/null)" ]; then
        echo "[bootstrap] 使用离线依赖包..."
        for whl in /opt/python-deps/*.whl; do
            if [ -f "$whl" ]; then
                python3 -c "
import zipfile
with zipfile.ZipFile('$whl') as z:
    for f in z.namelist():
        if '.data/' not in f and not f.endswith('.pyc'):
            z.extract(f, '$PY_SITE')
" 2>/dev/null
            fi
        done
    fi
fi

# dry-run 检测
DRY_RUN_SUPPORTED=0
if command -v python3 >/dev/null 2>&1; then
    if python3 -m pip install --help 2>&1 | grep -F -q -- '--dry-run'; then
        DRY_RUN_SUPPORTED=1
    fi
fi

# 安装各 agent 依赖
for name in $AGENTS; do
    reqfile="/opt/agents-requirements/${name}.txt"
    if [ ! -f "$reqfile" ]; then
        continue
    fi
    set +e

    if [ "$name" = "reasonix" ]; then
        current_ver=$(python3 -m pip show deepseek-reasonix 2>/dev/null | grep Version | awk '{print $2}')
        if [ -n "$current_ver" ]; then
            ver_ok=$(python3 -c "
v = '$current_ver'.split('.')
try:
    major = int(v[0])
    minor = int(v[1]) if len(v) > 1 else 0
    print('yes' if major > 0 or (major == 0 and minor >= 2) else 'no')
except:
    print('no')
" 2>/dev/null)
            if [ "$ver_ok" = "yes" ]; then
                echo "[bootstrap] reasonix $current_ver >= 0.2.0, 跳过升级"
                continue
            fi
        fi
        python3 -m pip install --upgrade --no-cache-dir -r "$reqfile" 2>&1 || \
            echo "[bootstrap] 警告: reasonix 安装失败"
    else
        if [ "$NET_OK" = "1" ] && [ "$DRY_RUN_SUPPORTED" = "1" ]; then
            if python3 -m pip install --dry-run --no-cache-dir -r "$reqfile" 2>&1; then
                echo "[bootstrap] $name 依赖已满足，跳过"
                continue
            fi
        fi
        python3 -m pip install --no-cache-dir -r "$reqfile" 2>&1 || \
            echo "[bootstrap] 警告: $name 安装失败"
    fi
done

echo "[bootstrap] 完成。"