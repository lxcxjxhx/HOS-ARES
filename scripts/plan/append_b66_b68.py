#!/usr/bin/env python3
"""一次性：把 B66 / B67 / B68 追加进 .veripen/batches.jsonl（只增不改）。"""
from __future__ import annotations

import json
from pathlib import Path

LEDGER = Path(__file__).resolve().parents[2] / ".veripen" / "batches.jsonl"

ROWS = [
    {
        "batch": "B66",
        "state": "done",
        "started": "2026-11-05",
        "ended": "2026-11-05",
        "agent": "little-code-sauce",
        "scope": "端到端渗透：契约→路由→oracle→capsule→报告，一条命令给交付物",
        "files": [
            "veripen_cli/cmd_run.py",
            "veripen_cli/cmd_hunt.py",
            "veripen_core/routes/sources/symbols.py",
            "tests/integration/test_run_e2e.py",
        ],
        "dod": "A(脆弱靶场退出0且产出report/findings/capsules) OK；B(安全靶场无reported) OK；C(两次结论一致) OK；D(报告无完整URL) OK；E(空契约非0) OK",
        "evidence": "pytest tests/integration/test_run_e2e.py -> 6 passed；全量 unit 绿；doctor fail=0",
        "open": "path_traversal 需 SAFE_FILE 符号，靶场未给时为 BLOCKED（诚实降级）",
        "notes": "修掉让真实目标必然0 oracle的缺陷：{p} 占位符因 param_names 缺路径参数被判未知符号，一条坏路由使整份契约0绑定；并把 build_guard 抽到 hunt 供两入口共用（第一版漏装 GuardContext，6 个 oracle 全抛 GuardMissing）",
    },
    {
        "batch": "B67",
        "state": "done",
        "started": "2026-11-05",
        "ended": "2026-11-05",
        "agent": "little-code-sauce",
        "scope": "交互会话：指令白名单 read/write/edit/sh，每步落 task_events，中断可恢复",
        "files": [
            "veripen_core/agent/session.py",
            "veripen_cli/cmd_agent.py",
            "tests/unit/test_agent_session.py",
        ],
        "dod": "A(连续指令累计上下文，失败动作不进上下文) OK；B(每步显示 tool→结果，无删除能力) OK；C(Ctrl+C 后事件仍在，--resume 读回含失败步) OK；D(未接模型明确说 scripted) OK",
        "evidence": "pytest tests/unit/test_agent_session.py -> 8 passed；手工冒烟 write→read→resume 三步可读",
        "open": "",
        "notes": "与 TUI 分工：TUI 只读三面板给看，会话给干；两者共用 task_events 作唯一真相源，会话不另存状态",
    },
    {
        "batch": "B68",
        "state": "done",
        "started": "2026-11-05",
        "ended": "2026-11-05",
        "agent": "little-code-sauce",
        "scope": "企业级编排：多资产 + 审批阻断 + 汇总报告四节 + 智能体行为审计五表",
        "files": [
            "veripen_cli/cmd_platform.py",
            "tests/integration/test_platform_e2e.py",
        ],
        "dod": "A(2 资产跑完出汇总报告) OK；B(未签字停 PENDING 且 approvals/task_events 有痕) OK；C(审计节五表齐全，空表显式) OK；D(playbook 状态写进报告，未命中写原因) OK；E(每资产独立 capsule 目录) OK",
        "evidence": "pytest tests/integration/test_platform_e2e.py -> 7 passed；手工实跑 out/demo-platform/platform-report.md",
        "open": "",
        "notes": "两处调用错误在报告里如实暴露后修掉：validate_against_oracles 第二参是 {oracle_id: impact} 映射不是类列表；findings.json 字段是 ruleId/evidence.capsule_id",
    },
]


def main() -> int:
    existing = {
        json.loads(line)["batch"]
        for line in LEDGER.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    added = 0
    with LEDGER.open("a", encoding="utf-8") as fh:
        for row in ROWS:
            if row["batch"] in existing:
                print(f"skip {row['batch']}（已存在）")
                continue
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            added += 1
    print(f"appended {added} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
