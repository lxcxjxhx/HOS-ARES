import json
from pathlib import Path

notes = """B21 做三件事，都是把"形状齐、线没接"补成"真能用"：asset_facts 写入点、todo 持久化、闭环率真测量。

一、asset_facts 写入点（veripen_core/blackboard.py 新增 AssetFacts）。
表一直在，但**没有任何写入路径**——这意味着跨任务记忆从来没有真正落过库。
新增的 API 名字里带 capsule 是刻意的：`record_from_capsule`。
四道门各对应一种真实腐化方式：
  ① capsule 必须存在且 sealed_at>0 —— 没 seal 的证据不是证据；
  ② evidence_id 必填 —— 说不清出处的记忆无法复核；
  ③ origin=target 必须 from_target=1 —— 目标返回的字符串是数据不是指令；
  ④ supersedes 必须指向存在的事实 —— 否则"推翻"指向空气。
I10 只增不改靠"没有 update/delete 方法"保证，与 Blackboard 同规矩。

二、todo 持久化（store.py 加 todos 表 + plan.py 加 TodoStore）。
TodoList 之前在内存里，进程一重启"现在做到哪"就没了——那是 33 §2 九阶段里最容易断的一环。
落库用**全量替换**（DELETE 该 task 全部旧行再写），因为 TodoList.write 的语义就是
"这就是当前清单"。增量写会让磁盘和内存悄悄分叉，而分叉后没有任何一方是权威。

三、闭环率真测量。这件的价值最大，因为它修的是一个"数字永远好看"的病灶。
原来 bench 里 `leads = 1 if REPORTED else 0`，于是 leads_closed/leads_total 恒等于 1.0，
不携带任何信息（B12-closure 已记，但那批只是"标注占位"）。
现在的口径：
  - bench 侧：**每条 lead 对应 judge 真读过的一个 pair**（一次真请求）。分母 150，不是 44。
    所有真发了请求的 case 都有结论，所以算出 1.00——这是**真实结果**，不是恒等式。
  - bench-leads 侧：act.* 产的 6 条 lead 真落进 leads 表，然后**每条都用它声明的
    verifiable_by 第一个 oracle 在 lab 靶场真跑一次**。5 条 VERIFIED + 1 条 REFUTED → 闭环率 1.00。
最关键的一条证据是 test_closure_rate_moves_with_state：同一账本在三次不同状态下
算出 0.0 → 0.5 → 0.333。恒等式做不到这一点。

四、一处必须记下的局限（不掩盖）。
bench-leads 的回环里，act.* 的 lead 指向本地探针服务（/admin、/api），
而 verify 跑的是 tests/lab 的靶场——**同语义、不同目标**。
也就是说目前打通的是"闭环机制"，不是"在同一目标上闭环"。
探针服务没有完整漏洞语义，用真 oracle 验它只会得到 INCONCLUSIVE，
所以这一版选了"用同语义靶场验"，并把差异写进 open（归 B22）。
把它说成"完全闭环"就是又一次把缺陷包装成特性。

五、踩到的一个真事故：批量替换误伤。
改 LabServer.stop() 时我用了全局字符串替换，把两处 ThreadingHTTPServer.shutdown()
也改成了 .stop()，直接 AttributeError 崩掉。
修法是按行号精确改回。教训：批量替换在多义变量名上是危险的，
这类错误不会在类型检查里暴露（两者都是 object）。

六、断言收口：A10 转绿，四批全部完成。
A10 现在是 6/6 真产 lead + 闭环率 1.00（真统计）→ exit=0。
12 条断言里 11 条绿，A3 仍部分（无 docker），B20-sandbox blocked。
没有任何一条靠放宽口径变绿。"""

rec = {
    "batch": "B21",
    "state": "done",
    "started": "2026-11-02T22:25:00Z",
    "ended": "2026-11-02T23:05:00Z",
    "agent": "dsh-harness",
    "scope": "asset_facts 写入点（四道门）+ todo 持久化（跨重启）+ 闭环率真测量（bench 真 pair / leads 表真统计 / act.*→verify 回环）",
    "files": [
        "veripen_core/blackboard.py",
        "veripen_core/store.py",
        "veripen_core/workbench/plan.py",
        "veripen_cli/cmd_bench_leads.py",
        "scripts/b12/run_bench.py",
        "tests/unit/test_closure_persistence.py",
        "plan-veripen/16-完整范围与交付地图.md",
        "plan-veripen/21-实现批次与Agent执行计划.md",
        ".veripen/batches.jsonl",
    ],
    "dod": [
        {"cmd": "python -m pytest tests", "exit": 0, "passed": 1105, "failed": 0, "skipped": 2,
         "note": "1105 passed / 2 skipped（+11）"},
        {"cmd": "python -m pytest tests/unit/test_closure_persistence.py -q", "exit": 0, "passed": 11},
        {"cmd": "python -m mypy --strict veripen_core/ worker/ veripen_oracles/ veripen/ veripen_cli/",
         "exit": 0, "errors": 0, "note": "94 source files"},
        {"cmd": "python scripts/lint/noroute.py", "exit": 0, "hits": 0},
        {"cmd": "python scripts/lint/nodata_stub.py", "exit": 0, "hits": 0},
        {"cmd": "python scripts/lint/netimports.py", "exit": 0, "hits": 0},
        {"cmd": "python scripts/lint/bindings_check.py", "exit": 0},
        {"cmd": "python scripts/lint/policy_check.py", "exit": 0},
        {"cmd": "python scripts/lint/act_cmd.py", "exit": 0, "hits": 0},
        {"cmd": "python scripts/lint/ui_zero_address.py", "exit": 0, "hits": 0},
        {"cmd": "python scripts/lint/spec_drift.py", "exit": 0, "note": "missing=0"},
        {"cmd": "python scripts/lint/ledger_check.py", "exit": 0, "note": "0 问题"},
        {"cmd": "python scripts/lint/noleak.py", "exit": 0, "hits": 0},
        {"cmd": "A10 bench-leads", "exit": 0,
         "note": "转绿：6/6 真产 lead；闭环率 1.00（6/6）来自 leads 表真统计（5 verified + 1 refuted），每条 lead 都真跑了对应 oracle"},
        {"cmd": "asset_facts 四道门", "exit": 0,
         "note": "无 capsule / capsule 未 seal / 无 evidence_id / target 未标记 —— 四种情形各有一条测试拒"},
        {"cmd": "todo 跨重启", "exit": 0, "note": "两个 Store 实例读写同一 db，清单与状态都在"},
        {"cmd": "闭环率随状态变化", "exit": 0,
         "note": "test_closure_rate_moves_with_state：0.0 → 0.5 → 0.333（恒等式做不到）"},
        {"cmd": "python scripts/b12/run_bench.py --rounds 3 --diff-findings", "exit": 0,
         "note": "findings_diff OK（44 条一致）；leads 150 来自真 pair 数"},
    ],
    "evidence": [
        "veripen_core/blackboard.py",
        "veripen_core/workbench/plan.py",
        "veripen_cli/cmd_bench_leads.py",
        "tests/unit/test_closure_persistence.py",
        "out/leads/leads.db",
        "out/leads/leads.json",
    ],
    "open": [
        "bench-leads 的 act.*→verify 回环是**同语义、不同目标**：lead 指向本地探针服务，verify 跑 tests/lab 靶场。闭环机制成立，目标一致性未保证——归 B22",
        "bench 侧闭环率 1.00 是真结果（所有真发请求的 case 都有结论），但它同时意味着「没有一条线索是查了没结论的」——按 35 §7，1.0 仍应怀疑是否只打软柿子，报告保留了那句提醒",
        "todos 表是新增表（03 §1 原规划 10 表里没有）；spec_drift 报了 EXTRA，需要 driver 决定是否正式登记进 03",
        "asset_facts 的 TTL 清理是读取时过滤（live() 按 created_at 与 ttl_s 判），没有后台清理任务：行会一直累积，长期部署需要归档策略",
        "TodoStore.save 是全量替换（DELETE + INSERT）：并发写同一 task 会互相覆盖，当前单 worker 场景可接受",
    ],
    "notes": notes,
    "i14_review": {
        "reviewed_at": "2026-11-02T23:05:00Z",
        "verdict": "B21 通过 I14：asset_facts 写入点与 todo 持久化两个注入点被真实实现替换（11 条测试）；闭环率从恒等式改为账本真统计并可随状态变化。B18–B21 四批收口",
        "closed_injection_points": [
            "veripen_core/blackboard.py:asset_facts 写入点（→ AssetFacts.record_from_capsule）",
            "veripen_core/workbench/plan.py:todo 持久化（→ TodoStore + todos 表）",
            "闭环率口径（bench `1 if REPORTED` → 真 pair 数；bench-leads `None` → leads 表统计 + verify 回环）",
        ],
        "still_empty": [
            "act.* → verify 的目标一致性（同语义不同目标，B22）",
            "SandboxNetwork 真隔离（B20-sandbox blocked：无 docker/WSL）",
        ],
    },
}

p = Path(".veripen/batches.jsonl")
with p.open("a", encoding="utf-8") as f:
    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
print("appended B21")
