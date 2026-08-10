#!/bin/sh
# HOS-ARES 启动入口 —— 在 proot 的 Alpine Linux 环境内执行（自研版）。
# 由 Android 端经 pty-bridge 提供 PTY 后运行；退出 reasonix 后落到 shell。
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
# reasonix bash 沙箱：Android 无 bubblewrap，强制 [sandbox] bash = off，
# 否则所有 shell 命令被拒（Java 端已预置，此处防配置被覆盖）。
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

# 国内 DNS 优先（8.8.8.8 在国内网络常不可达）
cat > /etc/resolv.conf <<DNS
nameserver 223.5.5.5
nameserver 119.29.29.29
DNS

# ============================================================
# HOS-ARES 三工具接入（Argus / PentestGPT / RepoAudit）—— 自研
# 全部预装进 rootfs；MCP 注册 + SKILL.md + 内置 memory，reasonix 启动即发现。
# 加新工具 = 一段 [[mcp.servers]] + 一个 SKILL.md + 一条 memory，无硬编码。
# ============================================================
mkdir -p /root/tools /opt/hos-mcp /root/.reasonix/skills /root/.reasonix/memory/global

# ---- MCP bridge（argus / pentestgpt / repoaudit，纯 stdlib，NDJSON JSON-RPC 2.0）----
cat > /opt/hos-mcp/hos_mcp.py <<'PY'
#!/usr/bin/env python3
# HOS-ARES MCP bridge: python3 hos_mcp.py <argus|pentestgpt|repoaudit>
# JSON-RPC 2.0 over NDJSON；DeepSeek key 自动继承 reasonix 全局 .env。
import sys, json, subprocess, os, time, urllib.request

NAME = sys.argv[1] if len(sys.argv) > 1 else "pentestgpt"
TIMEOUT = 3600
ARGUS_URL = "http://127.0.0.1:8081"

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
        env.setdefault("OPENAI_BASE_URL", "https://api.deepseek.com")
        env.setdefault("OPENAI_API_BASE", "https://api.deepseek.com")
        env.setdefault("GPT_MODEL_NAME", "deepseek-chat")
    return env

TOOLS = {
    "argus": [{
        "name": "redteam_scan",
        "description": "Argus AI-agent red-team scan: 500+ adversarial probes (OWASP LLM Top10, MITRE ATLAS, TAP/PAIR/GCG) against a target AI endpoint. Arguments: target (required, JSON {'kind':'openai_compat','base_url':'...','api_key_env':'...','model':'...'} or spec file path), probes (optional, default 'all'), format (optional html|sarif|junit, default sarif). Judge 用 DeepSeek（自动继承）。",
        "inputSchema": {"type": "object", "properties": {
            "target": {"type": "string"}, "probes": {"type": "string"}, "format": {"type": "string"}},
            "required": ["target"]},
    }],
    "pentestgpt": [{
        "name": "run_pentestgpt",
        "description": "Run PentestGPT penetration-testing task (legacy agent; DeepSeek 自动继承). Arguments: task (required), workspace (optional, default /root).",
        "inputSchema": {"type": "object", "properties": {
            "task": {"type": "string"}, "workspace": {"type": "string"}},
            "required": ["task"]},
    }],
    "repoaudit": [{
        "name": "audit_repo",
        "description": "Run RepoAudit repository-level code audit (NPD/Memory-Leak/UAF; C/C++/Java/Python/Go; DeepSeek 自动继承). Arguments: path (required, code directory).",
        "inputSchema": {"type": "object", "properties": {
            "path": {"type": "string"}},
            "required": ["path"]},
    }],
}

def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

def run(cmd, cwd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT, cwd=cwd, env=deepseek_env())
        out = (r.stdout or "") + ("\n[stderr]\n" + r.stderr if r.stderr else "")
        return {"content": [{"type": "text", "text": (out or "(no output)")[-20000:]}]}
    except subprocess.TimeoutExpired:
        return {"content": [{"type": "text", "text": "timeout after %ds" % TIMEOUT}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": "error: %s" % e}]}

def ensure_argus_server():
    try:
        urllib.request.urlopen(ARGUS_URL + "/docs", timeout=2)
        return
    except Exception:
        pass
    try:
        log = open("/root/tools/argus-server.log", "a")
        subprocess.Popen(["python3", "-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", "8081"],
                         cwd="/opt/argus/orchestrator", stdout=log, stderr=log,
                         env=deepseek_env(), start_new_session=True)
        for _ in range(30):
            time.sleep(1)
            try:
                urllib.request.urlopen(ARGUS_URL + "/docs", timeout=1)
                return
            except Exception:
                pass
    except Exception:
        pass

def call(name, args):
    if NAME == "argus" and name == "redteam_scan":
        target = str(args.get("target", ""))
        probes = str(args.get("probes", "all"))
        fmt = str(args.get("format", "sarif"))
        ensure_argus_server()
        out = "/root/tools/argus-report." + fmt
        cmd = ["argus-probe", "run", "--target", target, "--probes", probes,
               "--format", fmt, "--out", out, "--api-url", ARGUS_URL, "--token", "local"]
        res = run(cmd, "/root")
        res["content"][0]["text"] += "\nReport written to " + out
        return res
    if NAME == "pentestgpt" and name == "run_pentestgpt":
        task = str(args.get("task", ""))
        ws = str(args.get("workspace", "/root"))
        return run(["sh", "-c", "printf '%s\\n' \"$1\" | pentestgpt-legacy --base-url https://api.deepseek.com 2>&1", "hos", task], ws)
    if NAME == "repoaudit" and name == "audit_repo":
        path = str(args.get("path", ""))
        return run(["python3", "/opt/repoaudit/src/repoaudit.py", "--path", path], "/opt/repoaudit")
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
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS[NAME]}})
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
command -v argus-probe >/dev/null 2>&1 && echo "argus-probe OK" || echo "argus-probe 缺失"
[ -d /opt/argus/orchestrator ] && echo "argus orchestrator OK" || echo "argus orchestrator 缺失"
command -v pentestgpt-legacy >/dev/null 2>&1 && echo "pentestgpt-legacy OK" || echo "pentestgpt-legacy 缺失"
[ -f /opt/repoaudit/src/repoaudit.py ] && echo "repoaudit OK" || echo "repoaudit 缺失"
python3 -c "import openai, anthropic, litellm, fastapi, tree_sitter_c, pydantic_core" 2>/dev/null && echo "核心依赖 import OK" || echo "依赖 import 缺失"
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
reg_mcp "pentestgpt" "python3" "/opt/hos-mcp/hos_mcp.py" "pentestgpt"
reg_mcp "repoaudit" "python3" "/opt/hos-mcp/hos_mcp.py" "repoaudit"
reg_mcp "tengu" "tengu"
reg_mcp "mcts" "mcts-mcp"

# ---- Skills（说明"何时用/怎么用"）----
mkdir -p /root/.reasonix/skills/argus /root/.reasonix/skills/pentestgpt /root/.reasonix/skills/repoaudit \
         /root/.reasonix/skills/tengu /root/.reasonix/skills/mcts /root/.reasonix/skills/ghostprobe \
         /root/.reasonix/skills/mitmproxy /root/.reasonix/skills/zap
cat > /root/.reasonix/skills/argus/SKILL.md <<'MD'
---
name: argus
description: Argus AI 红队测试（gy15901580825/Argus，LLM 驱动），已注册 MCP server "argus"。用户要求对 AI 端点/LLM 应用做安全测试、prompt 注入/越狱对抗测试时调用 redteam_scan。judge 自动用 reasonix 的 DeepSeek key。
---
# Argus 使用
- MCP server `argus`：tool `redteam_scan(target, probes, format)`。
  - target：JSON {"kind":"openai_compat","base_url":"<目标AI端点>","api_key_env":"<env>","model":"<模型>"} 或 spec 文件路径。
  - probes：默认 all；format：sarif/html/junit，报告写 /root/tools/argus-report.<fmt>。
- 首次调用自动拉起本地 orchestrator（uvicorn :8081，日志 /root/tools/argus-server.log）。
- 工具已预装进 rootfs；自检日志 /root/tools/install.log。
MD
cat > /root/.reasonix/skills/pentestgpt/SKILL.md <<'MD'
---
name: pentestgpt
description: 自动化渗透测试专家（GreyDGL/PentestGPT），已注册 MCP server "pentestgpt"。用户要求渗透测试/CTF/Web 安全评估时调用 run_pentestgpt；深度交互用 CLI pentestgpt-legacy。LLM 自动用 reasonix 的 DeepSeek。
---
# PentestGPT 使用
- MCP server `pentestgpt`：tool `run_pentestgpt(task, workspace)`。
- CLI 兜底：`pentestgpt-legacy`（交互式，TUI 内直接运行）。
- 工具已预装进 rootfs；自检日志 /root/tools/install.log。
MD
cat > /root/.reasonix/skills/repoaudit/SKILL.md <<'MD'
---
name: repoaudit
description: 仓库级代码审计（PurCL/RepoAudit，多智能体 LLM），已注册 MCP server "repoaudit"。用户要求审计代码仓库/找空指针/内存泄漏/UAF 等 bug 时调用 audit_repo（参数 path）。支持 C/C++/Java/Python/Go。LLM 自动用 reasonix 的 DeepSeek。
---
# RepoAudit 使用
- MCP server `repoaudit`：tool `audit_repo(path)`，执行 python3 /opt/repoaudit/src/repoaudit.py --path <dir>。
- 审计手机上的代码用 /sdcard 路径；工具已预装进 rootfs。
MD

cat > /root/.reasonix/skills/tengu/SKILL.md <<'MD'
---
name: tengu
description: Tengu AI 协同渗透测试副驾驶（rfunix/tengu，FastMCP），已注册 MCP server "tengu"。用户要求自动侦察/扫描/生成报告、AI 编排多工具渗透测试时调用。LLM 自动继承 reasonix 的 DeepSeek。
---
# Tengu 使用
- MCP server `tengu`：启动即注册全部工具（FastMCP stdio），reasonix 自动发现。
- 特点：AI 编排——根据前一步发现决定下一步工具；内置工作流与 OWASP 资源。
- 依赖的 sslyze/nassl 已预装（TLS 扫描可用）；工具已预装进 rootfs。
MD
cat > /root/.reasonix/skills/mcts/SKILL.md <<'MD'
---
name: mcts
description: MCTS 模型上下文威胁扫描器（MCP-Audit/MCTS），已注册 MCP server "mcts"。用户要求审计 MCP 服务器/工具链安全、检测注入风险/权限问题/攻击链时调用。LLM 分析自动继承 reasonix 的 DeepSeek。
---
# MCTS 使用
- MCP server `mcts`：tool 由 mcts-mcp 提供（mcts.mcp_server），reasonix 自动发现。
- 也提供 CLI：`mcts scan <目标>`（本地静态扫描，无需 LLM）；`--llm-triage` 用 DeepSeek 分析。
- 工具已预装进 rootfs。
MD
cat > /root/.reasonix/skills/ghostprobe/SKILL.md <<'MD'
---
name: ghostprobe
description: ghostprobe 动态红队探测（OWASP MCP Top 10）。用户要求对 MCP 工具列表做安全探测（工具投毒/隐藏指令/危险能力/泄露三要素）时调用。CLI 工具（非 MCP server）：ghostprobe scan-file <tools.json>。
---
# ghostprobe 使用
- CLI：`ghostprobe scan-file tools.json`（分析 tools/list dump）；`ghostprobe scan-live <server>` 需要 mcp SDK（已装）。
- 定位：看服务器向 agent 广告的工具面，映射 OWASP MCP Top 10。
- 工具已预装进 rootfs（/opt/ghostprobe）。
MD

cat > /root/.reasonix/skills/mitmproxy/SKILL.md <<'MD'
---
name: mitmproxy
description: mitmproxy 开源中间人代理（Burp Suite 平替，MIT 协议）。用户要求抓包/拦截/重放 HTTP(S) 流量、分析 API 请求时调用。CLI 工具：mitmdump（命令行抓包，推荐）、mitmproxy（交互式 TUI）、mitmweb（Web 界面）。
---
# mitmproxy 使用
- 抓包：`mitmdump -p 8080`（监听 8080，手机浏览器/App 设代理 http://<本机IP>:8080 即可）。
- 重放/修改：`mitmdump -p 8080 -s <脚本.py>`（Python 脚本拦截改包，见 addons）。
- Web 界面：`mitmweb -p 8080`，手机浏览器访问 http://127.0.0.1:8081 可视化查看流量。
- 证书：首次 https 抓包需安装 mitmproxy CA（~/.mitmproxy/mitmproxy-ca-cert.pem）。
- 已预装进 rootfs（含 mitmproxy_rs 原生核心，gcompat 兼容层运行）。
MD

cat > /root/.reasonix/skills/zap/SKILL.md <<'MD'
---
name: zap
description: OWASP ZAP 主动安全扫描器（headless，功能最全的 Burp 平替）。用户要求主动漏洞扫描/爬虫/被动扫描 Web 应用时调用。CLI：zap-daemon 启动 headless 服务（:8082），REST API 操作。
---
# ZAP 使用
- 启动：`zap-daemon`（headless，监听 127.0.0.1:8082，API key: hosares）。首次启动初始化 20-60 秒。
- 扫描流程（REST API，curl 自签证书加 -k）：
  1. `curl -k "https://127.0.0.1:8082/JSON/ascan/action/scan/?apikey=hosares&url=<目标URL>&recurse=true"`（主动扫描）
  2. 状态：`curl -k "https://127.0.0.1:8082/JSON/ascan/view/status/?apikey=hosares&scanId=<id>"`
  3. 结果：`curl -k "https://127.0.0.1:8082/JSON/ascan/view/scanProgress/?apikey=hosares&scanId=<id>"`
- 也可 spider（`/JSON/spider/action/scan/`）先爬后扫。
- 已预装（/opt/zap + Java 17 JRE）；Java 内存默认即可，大目标可 JAVA_OPTS="-Xmx1g"。
MD
echo "[hos] skills 已注册: argus/pentestgpt/repoaudit/tengu/mcts/ghostprobe/mitmproxy/zap"

# ---- 内置 memory（reasonix 会话启动即知三工具）----
cat > /root/.reasonix/memory/global/hos-argus.md <<'MM'
---
name: hos-argus
title: Argus AI 红队测试
description: HOS-ARES 内置 Argus（gy15901580825/Argus，AI 红队）已接入：MCP server "argus"（redteam_scan），judge 自动用 DeepSeek。
metadata:
  type: reference
  scope: global
---
**Why:** 用户要求对 AI 端点做安全对抗测试（prompt 注入/越狱/数据泄露探针）时，reasonix 调 Argus。
**How to apply:** MCP tool `redteam_scan(target, probes, format)`；首次调用自动拉起本地 orchestrator（:8081）。工具已预装。
MM
cat > /root/.reasonix/memory/global/hos-pentestgpt.md <<'MM'
---
name: hos-pentestgpt
title: PentestGPT 渗透测试
description: HOS-ARES 内置 PentestGPT（GreyDGL/PentestGPT）已接入：MCP server "pentestgpt"（run_pentestgpt），CLI pentestgpt-legacy。LLM 自动继承 DeepSeek。
metadata:
  type: reference
  scope: global
---
**Why:** 用户要求渗透测试/CTF/Web 安全评估时，reasonix 调 PentestGPT。
**How to apply:** MCP tool `run_pentestgpt(task, workspace)`；深度交互用 CLI `pentestgpt-legacy`。工具已预装。
MM
cat > /root/.reasonix/memory/global/hos-repoaudit.md <<'MM'
---
name: hos-repoaudit
title: RepoAudit 代码审计
description: HOS-ARES 内置 RepoAudit（PurCL/RepoAudit）已接入：MCP server "repoaudit"（audit_repo），入口 python3 /opt/repoaudit/src/repoaudit.py。LLM 自动继承 DeepSeek。
metadata:
  type: reference
  scope: global
---
**Why:** 用户要求审计代码仓库、找空指针/内存泄漏/UAF 等 bug 时，reasonix 调 RepoAudit。
**How to apply:** MCP tool `audit_repo(path)`；支持 C/C++/Java/Python/Go。工具已预装。
MM
cat > /root/.reasonix/memory/global/hos-tengu.md <<'MM'
---
name: hos-tengu
title: Tengu 渗透副驾驶
description: HOS-ARES 内置 Tengu（rfunix/tengu）已接入：MCP server "tengu"（FastMCP，AI 编排多工具渗透测试）。LLM 自动继承 DeepSeek。
metadata:
  type: reference
  scope: global
---
**Why:** 用户要求自动侦察/扫描/生成报告、AI 编排的渗透测试时，reasonix 调 Tengu。
**How to apply:** MCP server `tengu` 启动即注册全部工具；sslyze/nassl 已预装。工具已预装进 rootfs。
MM
cat > /root/.reasonix/memory/global/hos-mcts.md <<'MM'
---
name: hos-mcts
title: MCTS 威胁扫描
description: HOS-ARES 内置 MCTS（MCP-Audit/MCTS）已接入：MCP server "mcts"（mcts-mcp），审计 MCP 工具链安全。LLM 分析自动继承 DeepSeek。
metadata:
  type: reference
  scope: global
---
**Why:** 用户要求审计 MCP 服务器/工具链、检测注入/权限/攻击链风险时，reasonix 调 MCTS。
**How to apply:** MCP server `mcts` 的工具自动发现；CLI `mcts scan` 静态扫描，--llm-triage 用 DeepSeek。工具已预装进 rootfs。
MM
cat > /root/.reasonix/memory/global/hos-ghostprobe.md <<'MM'
---
name: hos-ghostprobe
title: ghostprobe 红队探测
description: HOS-ARES 内置 ghostprobe（OWASP MCP Top 10 动态探测，CLI）已接入：ghostprobe scan-file <tools.json>。LLM 不需要（纯规则分析）。
metadata:
  type: reference
  scope: global
---
**Why:** 用户要求对 MCP 工具列表做安全探测（工具投毒/隐藏指令/危险能力/泄露三要素）时，reasonix 调 ghostprobe。
**How to apply:** CLI `ghostprobe scan-file tools.json`（分析 tools/list dump）；scan-live 用 mcp SDK（已装）。工具已预装进 rootfs。
MM
cat > /root/.reasonix/memory/global/hos-mitmproxy.md <<'MM'
---
name: hos-mitmproxy
title: mitmproxy 抓包代理
description: HOS-ARES 内置 mitmproxy（Burp Suite 开源平替）已接入：CLI mitmdump/mitmproxy/mitmweb。抓包/重放/改包 HTTP(S) 流量。无需 LLM（纯工具）。
metadata:
  type: reference
  scope: global
---
**Why:** 用户要求抓包、分析/重放 HTTP(S) 请求、调试 API 时，reasonix 调 mitmproxy。
**How to apply:** `mitmdump -p 8080` 抓包（手机设代理）；`-s 脚本.py` 改包；`mitmweb` Web 界面。https 需装 CA（~/.mitmproxy/mitmproxy-ca-cert.pem）。已预装进 rootfs。
MM
cat > /root/.reasonix/memory/global/hos-zap.md <<'MM'
---
name: hos-zap
title: OWASP ZAP 主动扫描
description: HOS-ARES 内置 OWASP ZAP 2.17（headless，功能最全的 Burp 平替）已接入：zap-daemon 启动 :8082，REST API 主动扫描。无需 LLM（纯工具）。
metadata:
  type: reference
  scope: global
---
**Why:** 用户要求 Web 应用主动漏洞扫描/爬虫/被动扫描时，reasonix 用 ZAP（比仅侦察更深入）。
**How to apply:** `zap-daemon` 启动（首次 20-60s）；REST API `curl -k https://127.0.0.1:8082/JSON/ascan/action/scan/?apikey=hosares&url=<目标>`；先 spider 再 ascan。已预装（/opt/zap + Java 17 JRE）。
MM
cat > /root/.reasonix/memory/global/hos-capability-limits.md <<'MM'
---
name: hos-capability-limits
title: HOS-ARES 能力边界
description: HOS-ARES 已装工具与明确不可用项：已装 argus/pentestgpt/repoaudit/tengu/mcts/ghostprobe；未装 metasploit/docker/ollama/frida（frida 需 root 设备）；LLM 统一 DeepSeek。
metadata:
  type: reference
  scope: global
---
**已装（直接用）**：Argus(redteam_scan)、PentestGPT(run_pentestgpt)、RepoAudit(audit_repo)、Tengu(MCP tengu)、MCTS(MCP mcts)、ghostprobe(CLI)。LLM 统一 DeepSeek（DEEPSEEK_API_KEY 继承，base_url api.deepseek.com）。

**不可用（不要调用，会失败）**：
- metasploit / msfconsole：未安装（arm64 Android 无法运行），不要尝试。
- docker / podman：proot 环境无容器能力，不要调用。
- ollama / 本地 LLM：未安装；LLM 一律走 DeepSeek API，不要尝试本地模型。
- frida / objection / MobSF：未预装；仅当设备已 Root（Home 界面显示"已检测 Root"）时才可 apk 安装使用，未 root 时不要调用。
- 未预装的命令（如 nuclei 若未装）：先 `command -v <工具>` 确认存在再调用。

**若想扩展**：轻量工具可在 reasonix 内 `apk add nmap`（Alpine 源已配）或 `pip install sqlmap` 按需安装。
MM
echo "[hos] memory 已预置: hos-argus/hos-pentestgpt/hos-repoaudit/hos-tengu/hos-mcts/hos-ghostprobe/hos-capability-limits"

# ---- 环境说明（reasonix 项目记忆，启动自动加载）----
cat > /root/AGENTS.md <<'MD'
# HOS-ARES 环境说明

本环境是运行在 Android 手机上的 Alpine Linux proot 容器（HOS-ARES · AI 安全实验室）。
内置六个安全工具（全部预装，通过 MCP/CLI 注册，直接用对应 MCP 工具）：
- Argus（AI 红队）：redteam_scan
- PentestGPT：run_pentestgpt
- RepoAudit：audit_repo
- Tengu（渗透副驾驶）：MCP server "tengu"（FastMCP，AI 编排）
- MCTS（威胁扫描）：MCP server "mcts"
- ghostprobe（MCP 红队探测）：CLI ghostprobe scan-file <tools.json>
- mitmproxy（抓包代理，Burp 平替）：CLI mitmdump / mitmproxy / mitmweb
- OWASP ZAP（主动扫描，Burp 平替）：zap-daemon 启动 headless（:8082）→ REST API
LLM 统一使用 DeepSeek（reasonix 的 DEEPSEEK_API_KEY），无需额外配置。
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
  echo "警告: 未找到 reasonix（/usr/local/bin/reasonix）。"
fi

exec /bin/sh
