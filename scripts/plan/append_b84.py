#!/usr/bin/env python3
"""一次性：把 B84 追加进 .veripen/batches.jsonl（只增不改）。"""
from __future__ import annotations

import json
from pathlib import Path

LEDGER = Path(__file__).resolve().parents[2] / ".veripen" / "batches.jsonl"

ROW = {
    "batch": "B84",
    "state": "done",
    "started": "2026-11-05",
    "ended": "2026-11-05",
    "agent": "little-code-sauce",
    "scope": "无容器时的受限执行：Windows Job Object（进程数/内存上限 + 整组回收），isolation 如实标 job-object",
    "files": [
        "veripen_core/sandbox/win_job.py",
        "veripen_core/sandbox/runner.py",
        "veripen_cli/cmd_doctor.py",
        "tests/unit/test_sandbox_job.py",
    ],
    "dod": "A(有 docker 仍优先 docker) OK；B(无 docker 走 job-object 且如实标 isolation) OK；C(进程数上限真生效：上限4起8个被拒) OK；D(超时整组杀，tasklist 无残留) OK；E(doctor 区分 docker/job-object/none 三态) OK",
    "evidence": "pytest tests/unit/test_sandbox_job.py -> 6 passed；doctor --only sandbox 显示 job-object；select_executor().run(['python','-c','print(42)']) -> ok/0/job-object",
    "open": "E1 真隔离仍未验：Job Object 不建命名空间，写 /tmp 即写宿主 /tmp",
    "notes": "用户不提供 docker，要求自行想办法。本机无 docker/podman/nerdctl、WSL 未装、Docker Desktop 需管理员+重启。改用 Windows Job Object 提供资源隔离与整组回收；两处修正：AssignProcessToJobObject 权限应为 SET_QUOTA|TERMINATE（原写成 SET_INFORMATION|QUERY_INFORMATION 会静默失败）、拿不到句柄时如实报 assign_failed 不假装受限",
}


def main() -> int:
    existing = {
        json.loads(line)["batch"]
        for line in LEDGER.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    if ROW["batch"] in existing:
        print(f"skip {ROW['batch']}（已存在）")
        return 0
    with LEDGER.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(ROW, ensure_ascii=False) + "\n")
    print("appended 1 row")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
