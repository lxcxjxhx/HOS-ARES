#!/bin/sh
# HOS-ARES 启动入口 —— 在 proot 的 Alpine Linux 环境内执行（自研版）。
# 由 Android 端经 pty-bridge 提供 PTY 后运行；退出 reasonix 后落到 shell。
# 4 Agent 精简：Reasonix（基座）+ Argus（SAST）/ RepoAudit（代码审计）/ Strix（渗透）。
export HOME=/root
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export TERM=xterm-256color
export LANG=C.UTF-8
export REASONIX_TELEMETRY=0

cd /root || exit 1

if [ ! -e /root/.hos-ares-welcome ]; then
  echo ""
  echo "  ==============================================="
  echo "   HOS-ARES · AI 安全实验室"
  echo "   基于 DeepSeek-Reasonix 源码构建"
  echo ""
  echo "   首次使用请先运行:  reasonix setup"
  echo "  ==============================================="
  echo ""
  touch /root/.hos-ares-welcome
fi

# ---- 基础环境（自研，替代参考工程的 bash 包装）----
CONF="$HOME/.reasonix/config.toml"
if [ -f "$CONF" ] && grep -q 'bash *= *"enforce"' "$CONF" 2>/dev/null; then
    sed -i 's/bash *= *"enforce"/bash = "off"/' "$CONF" 2>/dev/null
fi

# Alpine 无 bash：创建 bash -> busybox ash 包装（reasonix 探测到 bash 即可用）。
if [ ! -e /usr/local/bin/bash ]; then
    cat > /usr/local/bin/bash <<'SH'
#!/bin/sh
exec /bin/busybox ash "$@"
SH
    chmod 755 /usr/local/bin/bash
fi
[ -e /bin/bash ] || ln -sf /usr/local/bin/bash /bin/bash

# 国内 DNS 优先
cat > /etc/resolv.conf <<DNS
nameserver 223.5.5.5
nameserver 119.29.29.29
DNS

# ============================================================
# HOS-ARES 三工具接入（Argus / RepoAudit / Strix）—— 自研
# 全部预装进 rootfs；MCP 注册 + SKILL.md + 内置 memory，reasonix 启动即发现。
# ============================================================
mkdir -p /root/tools /opt/hos-mcp /root/.reasonix/skills /root/.reasonix/memory/global

# ---- MCP bridge（argus / repoaudit / strix，纯 stdlib，NDJSON JSON-RPC 2.0）----
# 关键修复：路径统一指向 /opt/agents/<name> 下的真实源码（不再引用不存在的 /opt/argus 等）。
cat > /opt/hos-mcp/hos_mcp.py <<'PY'
#!/usr/bin/env python3
# HOS-ARES MCP bridge: python3 hos_mcp.py <argus|repoaudit|strix>
# JSON-RPC 2.0 over NDJSON；DeepSeek key 自动继承 reasonix 全局 .env。
import sys, json, subprocess, os, time, urllib.request

NAME = sys.argv[1] if len(sys.argv) > 1 else "argus"
TIMEOUT = 3600

def deepseek_env():
    env = dict(os.environ)
    key = env.get("DEEPSEEK_API_KEY") or env.get("OPENAI_API_KEY") or ""
    if not key:
        try:
            with open(os.path.expanduser("~/.reasonix/.env")) as f:
                for line in f:
                    if line.strip().startswith("DEEPSEEK_API_KEY="):
                        key = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
        except Exception:
            pass
    if key:
        env.setdefault("DEEPSEEK_API_KEY", key)
        env.setdefault("OPENAI_API_KEY", key)
        env.setdefault("OPENAI_BASE_URL", env.get("HOS_LLM_BASE_URL", "https://api.deepseek.com"))
        env.setdefault("OPENAI_API_BASE", env.get("HOS_LLM_BASE_URL", "https://api.deepseek.com"))
        env.setdefault("GPT_MODEL_NAME", env.get("HOS_MODEL", "deepseek-v4-flash"))
    # llm_connect 别名：RepoAudit 用 DEEPSEEK_API_KEY2
    if key and not env.get("DEEPSEEK_API_KEY2"):
        env["DEEPSEEK_API_KEY2"] = key
    if key and not env.get("LLM_API_KEY"):
        env["LLM_API_KEY"] = key
    if not env.get("STRIX_LLM") and env.get("HOS_MODEL"):
        env["STRIX_LLM"] = env["HOS_MODEL"]
    env.setdefault("STRIX_TELEMETRY", "0")
    return env

TOOLS = {
    "argus": [{
        "name": "argus_scan_all",
        "description": "Argus 全量安全扫描（SAST/DAST/SCA/Secrets/IaC）。参数: target(必填, 目录或URL), format(可选 markdown|json|sarif, 默认 markdown)。无需 LLM Key。",
        "inputSchema": {"type": "object", "properties": {
            "target": {"type": "string"}, "format": {"type": "string"}},
            "required": ["target"]},
    }],
    "repoaudit": [{
        "name": "audit_repo",
        "description": "RepoAudit 仓库级代码审计（NPD/MLK/UAF 等，支持 C/C++/Java/Python/Go，需要 DeepSeek Key）。参数: path(必填, 代码目录), language(可选 Cpp|Java|Python|Go, 默认启发式推断)。",
        "inputSchema": {"type": "object", "properties": {
            "path": {"type": "string"}, "language": {"type": "string"}},
            "required": ["path"]},
    }],
    "strix": [{
        "name": "run_pentest",
        "description": "Strix AI 渗透测试（需要 DeepSeek Key）。参数: target(必填, URL/目标), instruction(可选, 用户指令), mode(可选 quick|standard|deep, 默认 quick)。",
        "inputSchema": {"type": "object", "properties": {
            "target": {"type": "string"}, "instruction": {"type": "string"}, "mode": {"type": "string"}},
            "required": ["target"]},
    }],
}

def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

def run(cmd, cwd, extra_env=None):
    try:
        env = deepseek_env()
        if extra_env:
            env.update(extra_env)
        # 把各 agent 源码目录加入 PYTHONPATH（与 reasonix_agent.py 一致）
        pp = env.get("PYTHONPATH", "")
        parts = [p for p in [
            "/opt/agents/argus/src",
            "/opt/agents/repoaudit/src",
            "/opt/agents/strix",
            "/opt/agents/reasonix",
        ] if p not in pp.split(":")]
        if pp:
            parts.append(pp)
        env["PYTHONPATH"] = ":".join(parts)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT, cwd=cwd, env=env)
        out = (r.stdout or "") + ("\n[stderr]\n" + r.stderr if r.stderr else "")
        return {"content": [{"type": "text", "text": (out or "(no output)")[-20000:]}]}
    except subprocess.TimeoutExpired:
        return {"content": [{"type": "text", "text": "timeout after %ds" % TIMEOUT}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": "error: %s" % e}]}

def call(name, args):
    if NAME == "argus" and name == "argus_scan_all":
        target = str(args.get("target", ""))
        fmt = str(args.get("format", "markdown"))
        return run(
            ["python3", "-m", "argus.cli", "scan", "all", target, "--format", fmt],
            "/opt/agents/argus/src",
        )
    if NAME == "repoaudit" and name == "audit_repo":
        path = str(args.get("path", ""))
        # RepoAudit 入口：python3 /opt/agents/repoaudit/src/repoaudit.py ...
        lang = str(args.get("language", "")).strip()
        cmd = [
            "python3", "/opt/agents/repoaudit/src/repoaudit.py",
            "--scan-type", "metascan",
            "--project-path", path,
        ]
        if lang in ("Cpp", "Java", "Python", "Go"):
            cmd += ["--language", lang]
        else:
            cmd += ["--language", "Python"]
        if os.environ.get("HOS_MODEL"):
            cmd += ["--model-name", os.environ["HOS_MODEL"]]
        return run(cmd, "/opt/agents/repoaudit/src")
    if NAME == "strix" and name == "run_pentest":
        target = str(args.get("target", ""))
        instr = str(args.get("instruction", ""))
        mode = str(args.get("mode", "quick"))
        cmd = [
            "python3", "-m", "strix.interface.cli",
            "-n", "-t", target,
            "--scan-mode", mode,
            "--max-budget", "10",
        ]
        if instr:
            cmd += ["--instruction", instr]
        return run(cmd, "/opt/agents/strix")
    return {"content": [{"type": "text", "text": "unknown tool: %s" % name}]}

def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        mid, method = msg.get("id"), msg.get("method")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "hos-%s" % NAME, "version": "1.0"}}})
        elif method == "notifications/initialized":
            pass
        elif method == "ping":
            send({"jsonrpc": "2.0", "id": mid, "result": {}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS.get(NAME, [])}})
        elif method == "tools/call":
            p = msg.get("params", {})
            send({"jsonrpc": "2.0", "id": mid, "result": call(p.get("name"), p.get("arguments", {}))})
        elif method == "shutdown":
            send({"jsonrpc": "2.0", "id": mid, "result": None})
            break

if __name__ == "__main__":
    main()
PY
chmod 755 /opt/hos-mcp/hos_mcp.py

# ---- 自检（全部预装，无需联网）----
cat > /root/tools/install-tools.sh <<'SH'
#!/bin/sh
exec > /root/tools/install.log 2>&1
echo "== HOS-ARES tools self-check $(date) =="
command -v python3 >/dev/null 2>&1 && python3 -V || { echo "python3 缺失"; exit 1; }
[ -f /opt/agents/argus/src/argus/cli.py ] && echo "argus src OK" || echo "argus src 缺失"
[ -f /opt/agents/repoaudit/src/repoaudit.py ] && echo "repoaudit src OK" || echo "repoaudit src 缺失"
[ -f /opt/agents/strix/strix/interface/cli.py ] && echo "strix src OK" || echo "strix src 缺失"
[ -f /opt/agents/reasonix/reasonix_agent.py ] && echo "reasonix_agent OK" || echo "reasonix_agent 缺失"
command -v reasonix >/dev/null 2>&1 && echo "reasonix CLI OK (deepseek-reasonix 已安装)" || echo "reasonix CLI 未安装(首次需联网 pip install deepseek-reasonix)"
echo "== self-check end $(date) =="
SH
chmod 755 /root/tools/install-tools.sh

# ---- MCP 注册（按 name 幂等）----
RX_CONF="$HOME/.reasonix/config.toml"
touch "$RX_CONF"
reg_mcp() {
    local n="$1"; shift
    if grep -q "name = \"$n\"" "$RX_CONF" 2>/dev/null; then
        echo "[hos] MCP '$n' 已注册"
    else
        {
            echo ""
            echo "# HOS-ARES: $n"
            echo "[[mcp.servers]]"
            echo "name = \"$n\""
            echo "command = \"$1\""
            [ -n "$2" ] && echo "args = [\"$2\"$( [ -n "$3" ] && printf ', "%s"' "$3")]"
            echo "call_timeout_seconds = 3600"
        } >> "$RX_CONF"
        echo "[hos] MCP '$n' 已注册"
    fi
}
reg_mcp "argus" "python3" "/opt/hos-mcp/hos_mcp.py" "argus"
reg_mcp "repoaudit" "python3" "/opt/hos-mcp/hos_mcp.py" "repoaudit"
reg_mcp "strix" "python3" "/opt/hos-mcp/hos_mcp.py" "strix"

# ---- Skills（说明"何时用/怎么用"，与 4 Agent 精简一致）----
mkdir -p /root/.reasonix/skills/argus /root/.reasonix/skills/repoaudit /root/.reasonix/skills/strix
cat > /root/.reasonix/skills/argus/SKILL.md <<'MD'
---
name: argus
description: Argus 全量安全扫描（无需 AI，SAST/DAST/SCA/Secrets/IaC），已注册 MCP server "argus"。用户要求扫描/漏洞/SAST/Secrets/SCA 时调用 argus_scan_all。
---
# Argus 使用
- MCP server `argus`：tool `argus_scan_all(target, format)`。
  - target 可以是目录（本地 SAST/SCA）或 URL（DAST）。
  - format 默认 markdown，可选 json/sarif。
- 工具源码路径：/opt/agents/argus/src；已预装。
MD
cat > /root/.reasonix/skills/repoaudit/SKILL.md <<'MD'
---
name: repoaudit
description: RepoAudit 仓库级代码审计（PurCL/RepoAudit，多智能体 LLM），已注册 MCP server "repoaudit"。用户要求审计代码仓库/找空指针/内存泄漏/UAF/bug 时调用 audit_repo（参数 path）。支持 C/C++/Java/Python/Go。LLM 自动继承 DeepSeek。
---
# RepoAudit 使用
- MCP server `repoaudit`：tool `audit_repo(path, language)`。
  - path 必填，language 可选（默认启发式按文件后缀推断）。
- 入口：python3 /opt/agents/repoaudit/src/repoaudit.py；已预装。
MD
cat > /root/.reasonix/skills/strix/SKILL.md <<'MD'
---
name: strix
description: Strix AI 渗透测试 Agent，已注册 MCP server "strix"。用户要求渗透/pentest/攻击/漏洞利用/CTF 时调用 run_pentest（参数 target）。LLM 自动继承 DeepSeek。
---
# Strix 使用
- MCP server `strix`：tool `run_pentest(target, instruction, mode)`。
  - mode：quick（默认，快）/standard/deep（更深）。
- 源码：/opt/agents/strix；也可用 CLI：`python3 -m strix -n -t <目标>`。
MD
echo "[hos] skills 已注册: argus/repoaudit/strix"

# ---- 内置 memory（reasonix 会话启动即知三工具）----
cat > /root/.reasonix/memory/global/hos-argus.md <<'MM'
---
name: hos-argus
title: Argus 全量安全扫描
description: HOS-ARES 内置 Argus 已接入：MCP server "argus"（tool argus_scan_all），无需 LLM，支持 SAST/DAST/SCA/Secrets/IaC。
metadata:
  type: reference
  scope: global
---
**Why:** 用户要求扫描/漏洞/SAST/代码扫描/Secrets/SCA 时，先调 Argus（快，无需 Key，覆盖广）。
**How to apply:** MCP tool `argus_scan_all(target, format)`。工具已预装，源码 /opt/agents/argus/src。
MM
cat > /root/.reasonix/memory/global/hos-repoaudit.md <<'MM'
---
name: hos-repoaudit
title: RepoAudit 代码审计
description: HOS-ARES 内置 RepoAudit（PurCL/RepoAudit）已接入：MCP server "repoaudit"（audit_repo），入口 /opt/agents/repoaudit/src/repoaudit.py。LLM 自动继承 DeepSeek（DEEPSEEK_API_KEY2）。
metadata:
  type: reference
  scope: global
---
**Why:** 用户要求审计代码仓库、找空指针/内存泄漏/UAF/符号执行/bug 时，reasonix 调 RepoAudit。
**How to apply:** MCP tool `audit_repo(path, language)`；支持 C/C++/Java/Python/Go。工具已预装。
MM
cat > /root/.reasonix/memory/global/hos-strix.md <<'MM'
---
name: hos-strix
title: Strix AI 渗透测试
description: HOS-ARES 内置 Strix 已接入：MCP server "strix"（run_pentest），源码 /opt/agents/strix；LLM 自动继承 DeepSeek（OPENAI_API_KEY / LLM_API_KEY，STRIX_LLM 对应 HOS_MODEL）。
metadata:
  type: reference
  scope: global
---
**Why:** 用户要求渗透测试/pentest/攻击/漏洞利用/CTF/Web 安全评估时，reasonix 调 Strix。
**How to apply:** MCP tool `run_pentest(target, instruction, mode)`。工具已预装。
MM
cat > /root/.reasonix/memory/global/hos-capability-limits.md <<'MM'
---
name: hos-capability-limits
title: HOS-ARES 能力边界（4 Agent 精简版）
description: HOS-ARES 已装工具与明确不可用项：已装 Argus/RepoAudit/Strix 三工具 + Reasonix 基座；Reasonix 基座通过 pip 可随时更新 deepseek-reasonix；未装 metasploit/docker/ollama/frida（frida 需 root 设备）；LLM 统一 DeepSeek。
metadata:
  type: reference
  scope: global
---
**已装（直接用 / MCP 已注册）**：
- Argus(SAST/DAST/SCA/Secrets/IaC) → MCP argus: argus_scan_all
- RepoAudit（NPD/MLK/UAF，符号执行 + LLM）→ MCP repoaudit: audit_repo
- Strix（AI 渗透，quick/standard/deep 模式）→ MCP strix: run_pentest
- Reasonix 基座（CLI `reasonix`，MCP server 框架 + skills/memory）
  - 基座更新：`pip install --upgrade deepseek-reasonix`，或重跑 bootstrap.sh
- LLM 统一 DeepSeek（DEEPSEEK_API_KEY 继承，base_url api.deepseek.com，模型 deepseek-v4-flash）

**不可用（不要调用，会失败）**：
- metasploit / msfconsole：未安装（arm64 Android 无法运行）
- docker / podman：proot 环境无容器能力
- ollama / 本地 LLM：未安装；LLM 一律走 DeepSeek API
- frida / objection / MobSF：未预装，仅 Root 设备可用
- deepaudit / pentestgpt / securityresearch：已从 HOS-ARES v2 精简下线（功能已并入 Argus/RepoAudit/Strix）
- 未预装命令：先 `command -v <工具>` 确认存在再调用

**扩展工具（运行时可装）**：Alpine `apk add nmap` 等轻量工具；Python `pip install sqlmap` 等纯 Python 包。
MM
echo "[hos] memory 已预置: hos-argus/hos-repoaudit/hos-strix/hos-capability-limits"

# ---- 环境说明（reasonix 项目记忆，启动自动加载）----
cat > /root/AGENTS.md <<'MD'
# HOS-ARES 环境说明

本环境是运行在 Android 手机上的 Alpine Linux proot 容器（HOS-ARES · AI 安全实验室）。
4 Agent 精简架构：
  - Reasonix：统一入口 / 基座 Agent 框架（deepseek-reasonix，pip 可更新）
  - Argus：全量安全扫描（SAST/DAST/SCA/Secrets/IaC，无需 LLM Key）
  - RepoAudit：仓库级代码审计（符号执行 + LLM，支持 C/C++/Java/Python/Go）
  - Strix：AI 渗透测试（多模式，DeepSeek 驱动）
三工具通过 MCP server（argus/repoaudit/strix）注册到 reasonix，skills/memory 已内置，直接调用对应 MCP tool 即可。

## Reasonix 基座更新
```
pip install --upgrade deepseek-reasonix
```
或者回到 App 主界面，APK 升级后 ASSETS_VERSION 变化会触发 bootstrap 自动重跑 pip install --upgrade。
MD

# ---- 工具自检（后台，不阻塞 reasonix）----
( /root/tools/install-tools.sh ) &

# ---- 会话恢复标记清理（保证每次全新进入 TUI）----
rm -f /root/.reasonix/projects/*/sessions/*.recovery.json \
      /root/.reasonix/projects/*/sessions/*.recovery \
      /root/.reasonix/projects/*/sessions/*.lease.* 2>/dev/null

# ---- 启动 reasonix；退出后落到 shell ----
if command -v reasonix >/dev/null 2>&1; then
  reasonix
  echo ""
  echo "Reasonix 已退出。输入 reasonix 重新启动，或 exit 关闭。"
else
  echo "警告: 未找到 reasonix CLI（/usr/local/bin/reasonix）。"
  echo "请联网执行：pip install --upgrade deepseek-reasonix"
fi

exec /bin/sh
