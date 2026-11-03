import json
from pathlib import Path

notes = """B19 只做一件事：把「执行」这根线接上，让手里的东西真能跑。

一、Executor 真实现（veripen_core/workbench/executor.py）。
签名与 shell.py 的 Executor 别名逐字一致，但这次是真起子进程：
- 超时必须杀（TimeoutExpired → 杀 → exit=124，与 timeout(1) 同约定）；
- 输出必须封顶（避免一个疯狂输出的脚本打爆内存）；
- 环境必须白名单（不继承宿主全部环境，否则一个 LD_PRELOAD 就能改行为）。
顺带修了 shell.py 里 elapsed_ms 硬编码 0 的老问题——那是「度量类写成常量」的又一个实例，不量墙钟就等于没测超时。

二、act.exec_tool 真跑（make_action_runner）。
在此之前 exec_tool 只产 argv、从不执行。现在真跑白名单二进制，做三件事：
真执行、用 08 §2 六步切片归一输出、从真实 stdout 派生命中数。
命中特征从「裸状态码」收紧到扫描器自己的结果行（[status: / [open] / found:）——
第一版用 200/403 这类裸码，把 ffuf banner 里的版本号也当成了命中，hits 从 2 变 3，多出来那条是假的。

三、地址怎么进 argv 却不违反「地址不入库」。
build_argv 拒任何含地址的 slots——那是拦模型的（35 §5）。但框架要把 route_ref 解析成真实目标，
这一步的正确位置是运行期：make_action_runner(resolve_host=...) 在**执行前一刻**把 argv 里的
占位 host（.invalid 后缀）换成解析值。地址既不进代码、也不进配置、更不由模型提供。
这里踩了一个坑：ffuf 的目标是 <base>/FUZZ，占位 host 只是前缀片段，
一开始按整 token 替换，导致 ffuf 去打一个不存在的整串主机——测出来「没命中」，
看起来像目标没洞，实际是接线没对。改成支持「前缀片段替换」。

四、A12 转绿，A10 到 4/6。
- A12 workbench-smoke：fs.write 真落盘、shell.run 真执行（stdout=veripen-smoke-ok）、plan 门生效。
- A10 bench-leads：exec_tool 真跑 ffuf 打本地服务得 2 命中产 lead（decode/compare/compose/exec_tool=4）。
- http_raw / http_fuzz 仍缺 transport（egress），归 B20；闭环率仍算不出（leads 表无写入方），归 B21。

五、没做的事，说清楚：沙箱不是本批。这里保证「真执行+真切片+真证据」，不保证「隔离」。
隔离（容器/命名空间）归 B20，会如实写进 scaffold_note，不用文档盖过去。"""

rec = {
    "batch": "B19",
    "state": "done",
    "started": "2026-11-02T20:15:00Z",
    "ended": "2026-11-02T20:45:00Z",
    "agent": "dsh-harness",
    "scope": "Executor 真实现（subprocess+超时杀+输出封顶+env 白名单+elapsed 实测）+ act.exec_tool 真跑（六步切片+真命中原生派生）",
    "files": [
        "veripen_core/workbench/executor.py",
        "veripen_core/workbench/shell.py",
        "veripen_cli/cmd_bench_leads.py",
        "tests/integration/test_executor.py",
        "plan-veripen/16-完整范围与交付地图.md",
        "plan-veripen/21-实现批次与Agent执行计划.md",
        ".veripen/batches.jsonl",
    ],
    "dod": [
        {"cmd": "python -m pytest tests", "exit": 0, "passed": 1085, "failed": 0, "skipped": 1,
         "note": "1085 passed / 1 skipped（+8）"},
        {"cmd": "python -m pytest tests/integration/test_executor.py -q", "exit": 0, "passed": 8},
        {"cmd": "python -m mypy --strict veripen_core/ worker/ veripen_oracles/ veripen/ veripen_cli/",
         "exit": 0, "errors": 0, "note": "92 source files"},
        {"cmd": "python scripts/lint/noroute.py", "exit": 0, "hits": 0, "note": "158 文件"},
        {"cmd": "python scripts/lint/nodata_stub.py", "exit": 0, "hits": 0},
        {"cmd": "python scripts/lint/netimports.py", "exit": 0, "hits": 0},
        {"cmd": "python scripts/lint/bindings_check.py", "exit": 0},
        {"cmd": "python scripts/lint/policy_check.py", "exit": 0},
        {"cmd": "python scripts/lint/act_cmd.py", "exit": 0, "hits": 0},
        {"cmd": "python scripts/lint/ui_zero_address.py", "exit": 0, "hits": 0},
        {"cmd": "python scripts/lint/spec_drift.py", "exit": 0, "note": "missing=0"},
        {"cmd": "python scripts/lint/ledger_check.py", "exit": 0, "note": "0 问题"},
        {"cmd": "python scripts/lint/noleak.py", "exit": 0, "hits": 0},
        {"cmd": "A12 workbench-smoke", "exit": 0,
         "note": "转绿：fs.write 27 字节真落盘；shell.run stdout=veripen-smoke-ok exit=0；plan 门拒 fs.write；五道闸正确"},
        {"cmd": "A10 bench-leads", "exit": 1,
         "note": "4/6：exec_tool 真跑 ffuf 打本地 ThreadingHTTPServer，命中 2（admin/api）产 lead；http_raw/http_fuzz 仍缺 transport（B20）；闭环率 None（B21）"},
        {"cmd": "shell.run 超时被杀", "exit": 0, "note": "sleep 5 / timeout 1 → exit=124，elapsed_ms>=500"},
        {"cmd": "env 白名单", "exit": 0, "note": "宿主 canary 变量不泄漏；显式 allowlist 变量可见"},
        {"cmd": "输出六步切片", "exit": 0, "note": "20000 行 → 切片后有 notice + persisted_ref，长度不超上限"},
        {"cmd": "non-whitelist 仍拒", "exit": 0, "note": "msfconsole/sqlmap/sliver/python/bash 全被 build_argv 拒"},
        {"cmd": "python scripts/b12/run_bench.py --rounds 3 --diff-findings", "exit": 0,
         "note": "findings_diff OK（44 条一致）；基线未动"},
    ],
    "evidence": [
        "veripen_core/workbench/executor.py",
        "tests/integration/test_executor.py",
        "out/leads/leads.json",
        "out/workbench/smoke.json",
    ],
    "open": [
        "沙箱不是本批：executor 起的是宿主同权限子进程，没有容器/命名空间隔离——归 B20",
        "exec_tool 的 resolve_host 由调用方注入；bench-leads 里接的是本地临时服务，生产形态需接 RouteBinding 的 resolver（同样在运行期）",
        "http_raw / http_fuzz 仍需 transport 注入（egress_allowlist + 沙箱），归 B20",
        "命中派生是行特征匹配（[status:/[open]/found:），不是结构化解析：换一个扫描器输出格式就要补特征，属已知上限",
        "run_shell 的 timeout 是 executor 内部 timeout；kill 后不再有二次回收，长驻子进程若 fork 孙进程可能残留——B20 沙箱用进程组解决",
    ],
    "notes": notes,
    "i14_review": {
        "reviewed_at": "2026-11-02T20:45:00Z",
        "verdict": "B19 通过 I14：Executor 与 exec_tool.run 两个注入点均被真实实现替换，8 条测试证明真执行/真超时/真切片/真命中；http transport 与沙箱仍空",
        "closed_injection_points": [
            "veripen_core/workbench/shell.py:Executor（→ executor.make_subprocess_executor）",
            "veripen_core/actions/__init__.py:exec_tool.run（→ executor.make_action_runner）",
        ],
        "still_empty": [
            "veripen_core/workbench/shell.py:SandboxNetwork 真隔离（B20）",
            "veripen_core/plugins/discovery.py:fetch（B20）",
            "veripen_core/blackboard.py:asset_facts 写入点（B21）",
            "veripen_core/workbench/plan.py:todo 持久化（B21）",
            "http_raw / http_fuzz 的 transport 注入（B20）",
        ],
    },
}

p = Path(".veripen/batches.jsonl")
with p.open("a", encoding="utf-8") as f:
    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
print("appended B19")
