#!/usr/bin/env python3
"""一次性：把 B60–B63 追加进 .veripen/batches.jsonl（只增不改）。"""
from __future__ import annotations

import json
from pathlib import Path

LEDGER = Path(__file__).resolve().parents[2] / ".veripen" / "batches.jsonl"

ROWS = [
    {
        "batch": "B60",
        "state": "done",
        "started": "2026-11-04",
        "ended": "2026-11-04",
        "agent": "little-code-sauce",
        "scope": "审批落库与事件：approvals 行 + task_events(kind=approval)，拒绝路径同样留痕",
        "files": [
            "veripen_core/guard/approval.py",
            "tests/unit/test_approval_persistence.py",
        ],
        "dod": "A(decide 后 approvals 行存在且 state 与内存一致) OK；B(每次 decision 追加一条 task_events kind=approval) OK；C(自批被拒且拒绝本身留痕) OK；D(TTL 过期转 EXPIRED 并写 expired 事件) OK；E(事件只增不改，seq 单调) OK",
        "evidence": "pytest tests/unit/test_approval_persistence.py -> 8 passed；tests/unit 全绿；parity 仍 green 20 / 未验 1 / 不做 1",
        "open": "",
        "notes": "负结论：store/events 为 None 时行为与 B54 逐字一致（纯内存），旧调用方零改动；自批检查前移到取 pending 之前，即使无待批记录也先留痕再抛",
    },
    {
        "batch": "B61",
        "state": "done",
        "started": "2026-11-04",
        "ended": "2026-11-04",
        "agent": "little-code-sauce",
        "scope": "doctor 三项运行时探测（sandbox/tui/browser），缺条件记 skip；顺带修 --json 与 argv 两个 CLI bug",
        "files": [
            "veripen_cli/cmd_doctor.py",
            "tests/unit/test_doctor_probes.py",
            "tests/unit/test_doctor.py",
        ],
        "dod": "A(无 docker/podman 时 sandbox=skip 且 fail=0) OK；B(有执行体时 sandbox=ok 并报类名) OK；C(无 curses 时 tui=skip) OK；D(无 playwright 时 browser=skip) OK；E(--json 含三项且 by_status 计入 skip) OK",
        "evidence": "pytest tests/unit/test_doctor_probes.py -> 7 passed；cmd_doctor --json 输出 ok16/fail0/skip3；tests/unit 全绿",
        "open": "",
        "notes": "负结论：skip 不算绿是 DoctorReport.green 的既有纪律，判据 A 据此改写；--json 与 --only 此前被 argparse 静默忽略（argv 为 None 时传空列表），修好后立刻暴露 GBK 编码错误，一并修",
    },
    {
        "batch": "B62",
        "state": "done",
        "started": "2026-11-04",
        "ended": "2026-11-04",
        "agent": "little-code-sauce",
        "scope": "L1a 压缩接线：injectable → compress → 带前缀段，接进 cmd_hunt 的 goal",
        "files": [
            "veripen_core/briefing.py",
            "veripen_cli/cmd_hunt.py",
            "tests/unit/test_l1a_injection.py",
        ],
        "dod": "A(injectable 出现生产调用方) OK；B(降幅 >=30%) OK；C(verdict 指纹逐字不变) OK；D(<=10 行且每条带 capsule_id) OK；E(过期标 stale 计数不删除) OK",
        "evidence": "pytest tests/unit/test_l1a_injection.py -> 9 passed；tests/unit 全绿；parity 仍 green 20 / 未验 1 / 不做 1",
        "open": "",
        "notes": "负结论：压缩必须\"先全量压、再截行\"——压 10 条小样本时合并同类项反而变长（k=v → k=v1|v2 (n=2)），降幅会算成负数，那是采样点选错不是算法失效；cmd_hunt 无库时用 :memory: 走同一路径，保证 injectable 在真实运行中始终被调",
    },
    {
        "batch": "B63",
        "state": "done",
        "started": "2026-11-04",
        "ended": "2026-11-04",
        "agent": "little-code-sauce",
        "scope": "plugin knowledge 进 L2：PluginSpec.knowledge → KnowledgeDoc → Context.with_knowledge，卸载即消失",
        "files": [
            "veripen_core/plugins/lifecycle.py",
            "veripen_core/briefing.py",
            "veripen_cli/cmd_hunt.py",
            "tests/unit/test_knowledge_wiring.py",
        ],
        "dod": "A(with_knowledge 出现生产调用) OK；B(装 100 条 verdict 指纹不变) OK；C(改全文指纹仍不变) OK；D(<=2 条/步、<=1KB/条，超限截断标记) OK；E(卸载后 L2 不再出现该正文) OK",
        "evidence": "pytest tests/unit/test_knowledge_wiring.py -> 8 passed；tests/unit 全绿；parity 仍 green 20 / 未验 1 / 不做 1；doctor ok16/fail0/skip3",
        "open": "",
        "notes": "负结论：Context.judge 恒为空，judge_input() 结构上拿不到知识段；knowledge_free 签名未改；卸载走 knowledge.pop，无缓存需失效——这是选\"单一真相源\"而非\"注册表+缓存\"的直接收益",
    },
]


def main() -> int:
    existing = {json.loads(line)["batch"] for line in LEDGER.read_text(encoding="utf-8").splitlines() if line.strip()}
    added = 0
    with LEDGER.open("a", encoding="utf-8") as fh:
        for row in ROWS:
            if row["batch"] in existing:
                print(f"skip {row['batch']}（已存在，账本只增不改）")
                continue
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            added += 1
    print(f"appended {added} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
