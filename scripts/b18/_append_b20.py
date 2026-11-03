import json
from pathlib import Path

notes = """B20 做三件事：插件网络、http 传输、进程组回收。第四件（容器隔离）本机做不了，如实拆出去。

一、插件 fetch（veripen_core/plugins/fetch.py，新）。
discovery 一直有完整的预算/etag/三分支新鲜度/排序公式，但从没有代码真去取过一条结果。现在接上：
- **预算先行**：先 spend 再发；超额抛错，不「先请求再记账」；
- **etag 真用**：带 If-None-Match，304 时只刷新 fetched_at、不动 results——
  覆盖会丢掉 etag 的语义，等于每次全量；
- **Result 无地址**：repository_url 只取 owner/name 两段，origin 丢掉。
ALLOWED_ORIGINS 是闭集（插件源是产品常量，不是客户资产地址——两者不是一回事）。

二、http 传输（veripen_core/actions/sender.py，新）。
http_raw / http_fuzz 一直收注入的 send 回调，但从没人给过真实现，所以 A10 卡在 3/6～4/6。
现在真发包：绑定模板 → 相对路径（自己填占位符，不复用 transport.render——那个要求完整 URL，
硬套会把「绑定渲染」和「运行期目标解析」混成一件事）→ resolve_base 给 host → perform 发出。
地址只在这一次调用栈里存在。

踩的坑值得记：第一版给 http_raw 和 http_fuzz 用了同一条带 {id} 的绑定，
结果 http_raw 不传参 → 模板残留 {id} → 抛「仍有未替换参数」。两条动作的参数合同不同，
必须各给一条绑定。第二版服务端对所有 /api?... 都回 404，基线和 probe 都没差异 → 差分恒 False
→ lead 天然作废。把探针服务改成「不同对象不同 body、999 被拒」之后差分才真实成立。
这两个坑的共同点：**看起来接线错了，其实是夹具语义不对**。

三、进程组回收。
B19 留的 open 是「脚本 fork 的孙进程会逃过 timeout 残留」。改成 Popen + 超时后 killpg
（POSIX）/ taskkill /T（Windows），并加了 start_new_session / CREATE_NEW_PROCESS_GROUP。

这里有一件必须写下来的事：**我先写的那条孙进程测试是假绿**。
探针脚本证明：Windows 上即使完全不做整组回收，孙进程在 TerminateProcess 之后也不存活。
也就是说那条断言在此平台上恒真、测不到任何东西。处理方式是标 POSIX-only skip 并写明原因，
不是留着它让「9 passed」看起来比实际强——那正是这个项目一直在防的那类假绿。

四、容器隔离：做不了，拆成 blocked。
本机 docker 无、WSL 无（实测）。所以 B20 的「沙箱真隔离」这半件不交付，
拆成 B20-sandbox 标 blocked，并写清开工条件：能起容器并证明「容器内直连外部 IP 失败、
走代理成功」。SandboxNetwork 仍是建模层，这一点在 A10 输出里显式打印「沙箱：未隔离」，
不用文档盖过去。

五、断言变化：A11 转绿、A10 到 6/6。
A10 现在唯一剩的是闭环率（leads 表无写入方，act.* 未接入 agent 循环），归 B21。
这条红是有价值的：它指着最后一个真正的空洞。"""

rec = {
    "batch": "B20",
    "state": "done",
    "started": "2026-11-02T21:00:00Z",
    "ended": "2026-11-02T22:00:00Z",
    "agent": "dsh-harness",
    "scope": "插件 fetch 真实现（etag/304/预算/无地址）+ act.http_* 真传输 + executor 进程组回收；容器隔离拆出为 blocked",
    "files": [
        "veripen_core/plugins/fetch.py",
        "veripen_core/actions/sender.py",
        "veripen_core/workbench/executor.py",
        "veripen_cli/cmd_bench_leads.py",
        "veripen_cli/cmd_plugin_roundtrip.py",
        "tests/integration/test_plugins_http.py",
        "tests/integration/test_executor.py",
        "plan-veripen/16-完整范围与交付地图.md",
        "plan-veripen/21-实现批次与Agent执行计划.md",
        ".veripen/batches.jsonl",
    ],
    "dod": [
        {"cmd": "python -m pytest tests", "exit": 0, "passed": 1094, "failed": 0, "skipped": 2,
         "note": "1094 passed / 2 skipped（+9；skip 之一为 POSIX-only 孙进程测试）"},
        {"cmd": "python -m pytest tests/integration/test_plugins_http.py -q", "exit": 0, "passed": 9},
        {"cmd": "python -m pytest tests/integration/test_executor.py -q", "exit": 0,
         "passed": 8, "skipped": 1},
        {"cmd": "python -m mypy --strict veripen_core/ worker/ veripen_oracles/ veripen/ veripen_cli/",
         "exit": 0, "errors": 0, "note": "94 source files"},
        {"cmd": "python scripts/lint/noroute.py", "exit": 0, "hits": 0, "note": "161 文件"},
        {"cmd": "python scripts/lint/nodata_stub.py", "exit": 0, "hits": 0},
        {"cmd": "python scripts/lint/netimports.py", "exit": 0, "hits": 0},
        {"cmd": "python scripts/lint/bindings_check.py", "exit": 0},
        {"cmd": "python scripts/lint/policy_check.py", "exit": 0},
        {"cmd": "python scripts/lint/act_cmd.py", "exit": 0, "hits": 0},
        {"cmd": "python scripts/lint/ui_zero_address.py", "exit": 0, "hits": 0},
        {"cmd": "python scripts/lint/spec_drift.py", "exit": 0, "note": "missing=0"},
        {"cmd": "python scripts/lint/ledger_check.py", "exit": 0, "note": "0 问题"},
        {"cmd": "python scripts/lint/noleak.py", "exit": 0, "hits": 0},
        {"cmd": "A11 plugin-roundtrip", "exit": 0,
         "note": "转绿：四类挂载分级准入 + 装知识不改判定 + remote-fetch 真验 etag/304/预算/无地址"},
        {"cmd": "A10 bench-leads", "exit": 1,
         "note": "6/6 达成：decode/compare/compose/exec_tool/http_raw/http_fuzz 全部真产 lead；唯一剩余警告是闭环率（B21）"},
        {"cmd": "http_fuzz 差分来自真响应", "exit": 0,
         "note": "基线与 probe 打本地 API：id=1 vs id=2 body 不同 → differential；id=999 → 403 vs 200"},
        {"cmd": "fetch 304 不覆盖缓存", "exit": 0, "note": "第二次请求带 If-None-Match，304 后 results 条数不变"},
        {"cmd": "fetch 预算挡第三次", "exit": 0, "note": "budget_per_refresh=2 时第三次抛错"},
        {"cmd": "python scripts/b12/run_bench.py --rounds 3 --diff-findings", "exit": 0,
         "note": "findings_diff OK（44 条一致）；基线未动"},
    ],
    "evidence": [
        "veripen_core/plugins/fetch.py",
        "veripen_core/actions/sender.py",
        "tests/integration/test_plugins_http.py",
        "out/leads/leads.json",
        "out/plugins/roundtrip.json",
    ],
    "open": [
        "容器隔离未交付：本机无 docker、无 WSL，B20-sandbox 标 blocked，开工条件是能起容器并证明「直连外部 IP 失败、经代理成功」",
        "SandboxNetwork 仍是建模层：它判定「只有回环代理端口可达」，但不实施任何网络过滤",
        "孙进程回收测试在 Windows 上 skip：实测该平台即使不整组回收孙进程也不存活，断言恒真（假绿），故标 POSIX-only",
        "http_fetch 只在闭集 origin（api.github.com）上工作；新增插件源要改 ALLOWED_ORIGINS，等于强制一次评审",
        "http_* sender 走宿主网络栈，没有 post_connect 对端核对（那是 tools/http.send 产品路径的职责）；动作层产物只能是 lead，不出 verdict",
        "探针服务是为验收写的本地夹具，它的「不同对象不同 body」是刻意行为，不是从真实靶场学来的",
    ],
    "notes": notes,
    "i14_review": {
        "reviewed_at": "2026-11-02T22:00:00Z",
        "verdict": "B20 通过 I14（部分）：plugins.fetch 与 http_raw/http_fuzz 的 send 两个注入点被真实实现替换并有 9 条测试；容器隔离这一半拆为 B20-sandbox blocked，不算完成",
        "closed_injection_points": [
            "veripen_core/plugins/discovery.py:fetch（→ fetch.http_fetcher / ScriptedFetcher）",
            "veripen_core/actions/__init__.py:http_raw/http_fuzz 的 send（→ sender.make_binding_sender / make_param_sender）",
        ],
        "still_empty": [
            "veripen_core/blackboard.py:asset_facts 写入点（B21）",
            "veripen_core/workbench/plan.py:todo 持久化（B21）",
            "SandboxNetwork 真隔离（B20-sandbox blocked：无 docker/WSL）",
        ],
    },
}

p = Path(".veripen/batches.jsonl")
with p.open("a", encoding="utf-8") as f:
    f.write(json.dumps(rec, ensure_ascii=False) + "\n")

sandbox = {
    "batch": "B20-sandbox",
    "state": "blocked",
    "started": "2026-11-02T21:50:00Z",
    "ended": None,
    "agent": "dsh-harness",
    "scope": "容器/命名空间真隔离：容器内直连外部 IP 失败、走代理成功",
    "files": [],
    "dod": [],
    "evidence": [],
    "open": [
        "本机实测：docker 不存在；wsl 存在但未安装任何发行版（wsl --install 提示）",
        "因此无法证明「容器内直连失败、走代理成功」这条 DoD；任何声称已隔离的说法都会是建模冒充",
        "开工条件：① 有可用 docker 或安装 WSL 发行版；② 能起一个容器并验证其网络命名空间；③ 有代理进程可做 egress 校验",
        "预备方案（未验证）：Windows Job Object + AppContainer 受限令牌，或 WSL 内 runc；两者都需要在新环境重测",
    ],
    "notes": "这条 blocked 是诚实记录，不是缺陷。B20 的另外三件（插件 fetch、http 传输、进程组回收）已完成并验收；"
             "唯独「隔离」这件事没法在本机验证，所以拆出来单列，不混进 B20 的 done。"
             "判断依据与 B13-offline 同一条：能测的测，不能测的写清为什么不能测，不用建模冒充真隔离。",
}

with p.open("a", encoding="utf-8") as f:
    f.write(json.dumps(sandbox, ensure_ascii=False) + "\n")
print("appended B20 + B20-sandbox")
