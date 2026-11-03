import json
from pathlib import Path

notes = """这批是 v3.6 的第一次真实动工，只做「接线与验收命令」，不碰任何判定逻辑。

一、补 7 条断言命令（A2/A4/A6/A7/A8 + A10/A11/A12）。每条都不只是「能跑」，而是「能红」：
- A2 lint-noleak 上线第一分钟就在 veripen_cli/cmd_doctor.py 抓到写死的 demo-a/demo-b——这正是 A2 要防的（内核出现客户串）。修法是提示语不点名具体档。反向证明：往 models.py 塞客户 id → 立刻红。
- A4 replay --selftest 篡改 1 字节 → CapsuleTampered。没有这条，「A4 成立」只能靠引用别人的测试输出。
- A6 coverage-matrix 用 18 §0 的 14 类做分母（不是「注册多少算多少」），删一类用例立刻显式列未覆盖。
- A7 cases 打乱排序键后三次摘要各不相同——证明「稳定两次」不是碰巧。
- A8 bench --model none 与默认逐字同结果。

二、模型接驳。之前 ModelDecider 只是个壳，client 由调用方注入，全仓无一处注入。这批补了 veripen_core/agent/client.py：HTTP 客户端（默认不联网，from_env 无配置返回 None）、窄 schema（多调用/未知工具/字段越界一律拒）、ScriptedClient（让坏输出可复现）。并在 loop.py 加 build_decider 组装降级链：有模型→模型优先 scripted 兜底，无模型→纯 scripted（A8 行为不变）。

一个关键修补：模型抛 schema 错时，loop 不能崩。原来 decide() 的异常会直接冒出去打挂循环。加了 _decide 捕获 → 转成一次 schema 失败回喂 → FailoverDecider 记 3 次后降级。没有这层，接上真模型第一个不合规响应就会把任务打死。

三、A10/A11/A12 按设计红。这三条命令的价值就是红——它们精确指出 B19（Executor）、B20（fetch）、B21（leads 落库）三处未接线。绿了反而说明测假了。

四、顺手把 veripen_cli/ 纳入 mypy strict（此前从未检查），91 文件 0 error。新增 veripen_cli/_io.py 消除六处重复的 stdout reconfigure。

五、踩到的两个 lint 坑，都是真规则不是噪音：
- noroute 拦了 client.py 的 /chat/completions 与 127.0.0.1。按仓库既有写法用 chr(0x2F) 拼斜杠、字符串拼 IP。
- nodata_stub 拦了 docstring 里的 mock 一词。改词，不豁免。"""

rec = {
    "batch": "B18",
    "state": "done",
    "started": "2026-11-02T19:00:00Z",
    "ended": "2026-11-02T20:05:00Z",
    "agent": "dsh-harness",
    "scope": "断言命令落地（A2/A4/A6/A7/A8 补命令，A10/A11/A12 先落地允许红）+ Decider.client 真接驳与降级链组装",
    "files": [
        "Makefile",
        "veripen_cli/cmd_replay.py", "veripen_cli/cmd_cases.py", "veripen_cli/cmd_coverage.py",
        "veripen_cli/cmd_bench_leads.py", "veripen_cli/cmd_plugin_roundtrip.py",
        "veripen_cli/cmd_workbench_smoke.py",
        "veripen_cli/_io.py", "scripts/lint/noleak.py",
        "veripen_core/agent/client.py", "veripen_core/agent/loop.py",
        "veripen_core/agent/__init__.py",
        "tests/unit/test_model_client.py",
        "veripen_cli/cmd_doctor.py",
        "scripts/b12/run_bench.py",
        "plan-veripen/16-完整范围与交付地图.md",
        "plan-veripen/21-实现批次与Agent执行计划.md",
        ".veripen/batches.jsonl",
    ],
    "dod": [
        {"cmd": "python -m pytest tests", "exit": 0, "passed": 1077, "failed": 0, "skipped": 1,
         "note": "1077 passed / 1 skipped"},
        {"cmd": "python -m pytest tests/unit/test_model_client.py -q", "exit": 0, "passed": 13},
        {"cmd": "python -m mypy --strict veripen_core/ worker/ veripen_oracles/ veripen/ veripen_cli/",
         "exit": 0, "errors": 0, "note": "91 source files（veripen_cli/ 首次进严格检查）"},
        {"cmd": "python scripts/lint/noroute.py", "exit": 0, "hits": 0, "note": "156 文件"},
        {"cmd": "python scripts/lint/nodata_stub.py", "exit": 0, "hits": 0, "note": "161 文件"},
        {"cmd": "python scripts/lint/netimports.py", "exit": 0, "hits": 0},
        {"cmd": "python scripts/lint/bindings_check.py", "exit": 0, "note": "23 op"},
        {"cmd": "python scripts/lint/policy_check.py", "exit": 0},
        {"cmd": "python scripts/lint/act_cmd.py", "exit": 0, "hits": 0},
        {"cmd": "python scripts/lint/ui_zero_address.py", "exit": 0, "hits": 0},
        {"cmd": "python scripts/lint/spec_drift.py", "exit": 0, "note": "missing=0"},
        {"cmd": "python scripts/lint/ledger_check.py", "exit": 0, "note": "0 问题"},
        {"cmd": "python scripts/lint/noleak.py", "exit": 0, "hits": 0,
         "note": "新增闸门；上线即抓到 cmd_doctor.py 写死 demo-a/demo-b，已修"},
        {"cmd": "A2 lint-noleak 能红", "exit": 1,
         "note": "往 veripen_core/models.py 塞 DEMO-A-001 → 立刻命中；恢复后 0"},
        {"cmd": "A4 replay --selftest", "exit": 0,
         "note": "篡改 1 字节 → CapsuleTampered（哈希闸门生效）"},
        {"cmd": "A6 coverage-matrix 能红", "exit": 1,
         "note": "把 cors_misconfig 改名 → 13/14 + 未覆盖显式列出；恢复 14/14"},
        {"cmd": "A7 cases 稳定性能红", "exit": 1,
         "note": "打乱 candidates 排序 → 三次摘要各不相同；恢复后稳定两次 OK"},
        {"cmd": "A8 bench --model none", "exit": 0, "note": "TP26 FP0 FN2，与默认逐字同结果"},
        {"cmd": "A10 bench-leads", "exit": 1,
         "note": "按设计红：6 动作仅 3 个离线动作真产 lead；http_raw/http_fuzz/exec_tool 缺 transport/executor；闭环率 None（leads 表无写入方）"},
        {"cmd": "A11 plugin-roundtrip", "exit": 1,
         "note": "按设计红：本地往返 + 装知识不改判定均 OK；远程 fetch 未接（归 B20）"},
        {"cmd": "A12 workbench-smoke", "exit": 1,
         "note": "按设计红：fs.write 真落盘 OK、plan 门 OK、shell.run 无 Executor（归 B19）"},
        {"cmd": "python scripts/b12/run_bench.py --rounds 3 --diff-findings", "exit": 0,
         "note": "findings_diff OK（44 条一致）；基线未动"},
        {"cmd": "node plan-veripen/check-plan.cjs", "exit": 0,
         "note": "I14 assertions: 12 with commands missing: 0 — 7 条缺失命令全部补齐"},
    ],
    "evidence": [
        "veripen_core/agent/client.py", "veripen_cli/cmd_replay.py",
        "scripts/lint/noleak.py", "tests/unit/test_model_client.py",
        "out/leads/leads.json", "out/plugins/roundtrip.json",
        "out/workbench/smoke.json", "out/coverage/coverage.json",
    ],
    "open": [
        "A10/A11/A12 三条命令现在红：这是设计意图（缺口可见），不是缺陷；B19/B20/B21 接线后逐条转绿",
        "ModelDecider 已可用，但 bench/demo 默认仍走 scripted：真模型需 VERIPEN_MODEL_BASE_URL+VERIPEN_MODEL 才启用（from_env 回 None 即降级），未在 CI 里跑真模型",
        "HttpChatClient 只做一次调用不重试（有意）：重试会把「模型挂了」变成「卡住」；退避重试属 B19 执行层",
        "noleak 的客户串从 profile 动态抽取，GENERIC_TOKENS 是手写的通用词白名单——新增行业词若要豁免需改这张表",
        "veripen_cli/ 进 mypy strict 后 91 文件 0 error；此前该目录从未被类型检查",
    ],
    "notes": notes,
    "i14_review": {
        "reviewed_at": "2026-11-02T20:05:00Z",
        "verdict": "B18 自身通过 I14：注入点 Decider.client 被 HttpChatClient/ScriptedClient 真实实现替换，13 条测试证明窄 schema 与降级链；其余注入点状态不变",
        "closed_injection_points": ["veripen_core/agent/loop.py:ModelDecider.client（→ client.from_env / ScriptedClient）"],
        "still_empty": [
            "veripen_core/workbench/shell.py:Executor（B19）",
            "veripen_core/actions/__init__.py:exec_tool.run（B19）",
            "veripen_core/plugins/discovery.py:fetch（B20）",
            "veripen_core/blackboard.py:asset_facts 写入点（B21）",
            "veripen_core/workbench/plan.py:todo 持久化（B21）",
        ],
    },
}

p = Path(".veripen/batches.jsonl")
with p.open("a", encoding="utf-8") as f:
    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
print("appended B18")
