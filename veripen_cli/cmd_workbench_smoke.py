#!/usr/bin/env python3
"""veripen workbench-smoke —— A12「工作台」的**验收命令**（`38`）。

用法：python -m veripen_cli.cmd_workbench_smoke [--out out/workbench]

A12 的实质是**两只手真的能拿东西**，不是闸门写得对。四条，前三条必须真发生：

1. **`fs.write` 真落盘**：写文件 → 从磁盘读回来比字节。闸门对但盘上没东西，手就是假的。
2. **`shell.run` 真执行**：通过 `Executor` 跑 `echo` 拿真 stdout。
   **产品侧目前没有 `Executor` 实现**（只有测试传入过）——所以这条会红，红得有意义。
3. **`plan.enter` 后写被拒**：`PlanGate.check("fs.write")` 必须抛。
4. **五道闸的形状**（这条现在就是绿的——它证明"闸门在"，不证明"手在"）。

第 2 条是本命令存在的主要理由：`39 §2` 说「注入点空只能是 `scaffolded`」，
这条命令就是那把尺——红 = 手没接上（B19）；绿 = 真接上了。
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from ._io import setup_stdout  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from veripen_core.workbench.fs import Workbench  # noqa: E402
from veripen_core.workbench.plan import (  # noqa: E402
    PlanError,
    PlanGate,
    PlanState,
)
from veripen_core.workbench.shell import (  # noqa: E402
    ACTION_REQUIREMENTS,
    ApprovalPolicy,
    SandboxMode,
)


def _probe_fs(work: Path) -> dict[str, object]:
    """fs.write / fs.read 真落盘往返。"""
    wb = Workbench(work)
    payload = "veripen-smoke-落盘验证\n"
    try:
        res = wb.write("smoke.txt", payload)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"write 抛错 {type(exc).__name__}: {exc}"}
    target = work / "smoke.txt"
    if not target.is_file():
        return {"ok": False, "error": "write 返回成功但盘上没有文件"}
    back = wb.read("smoke.txt").text
    return {
        "ok": back == payload,
        "bytes_written": res.bytes_written,
        "created": res.created,
        "roundtrip_equal": back == payload,
    }


def _probe_shell(work: Path) -> dict[str, object]:
    """shell.run 真执行。**没有 Executor 实现时如实报缺接线。**"""
    import importlib

    # B19 的注入点：模块不存在 = 手没接上。用 importlib 探，避免 mypy 因
    # "这个模块还不存在"而报 import-not-found——那会让"缺口"变成"类型错误"。
    try:
        mod = importlib.import_module("veripen_core.workbench.executor")
    except ImportError:
        return {
            "ok": False,
            "error": "no_executor_impl",
            "detail": "veripen_core.workbench.executor 不存在 → 归 B19",
        }
    make_subprocess_executor = mod.make_subprocess_executor
    # 若 B19 已接上，就走真执行路径
    try:
        from veripen_core.workbench.shell import Interp, ShellRequest, WorkbenchPolicy, run_shell

        executor = make_subprocess_executor()
        req = ShellRequest(script="echo veripen-smoke-ok", interpreter=Interp.BASH)
        res = run_shell(req, policy=WorkbenchPolicy(), executor=executor)
        return {"ok": res.exit_code == 0 and "veripen-smoke-ok" in res.stdout,
                "stdout": res.stdout.strip()[:60], "exit": res.exit_code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _probe_plan() -> dict[str, object]:
    """计划模式下写必须被拒。"""
    gate = PlanGate()
    try:
        gate.enter(reason="smoke: 验证计划模式确实挡住写")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"plan.enter 失败 {type(exc).__name__}: {exc}"}
    try:
        gate.check("fs.write")
    except PlanError as exc:
        return {"ok": gate.state is PlanState.PLANNING, "blocked": True, "reason": str(exc)[:60]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": False, "error": "PLANNING 状态下 fs.write 未被拒（计划门失效）"}


def _probe_shell_gates() -> dict[str, object]:
    need = ACTION_REQUIREMENTS.get("shell.run")
    if need is None:
        return {"ok": False, "error": "shell.run 未登记在 ACTION_REQUIREMENTS"}
    mode, approval = need
    return {
        "ok": mode is SandboxMode.WORKSPACE_WRITE and approval is ApprovalPolicy.ON_RISK,
        "sandbox_mode": mode.value,
        "approval": approval.value,
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="veripen workbench-smoke")
    ap.add_argument("--out", default=str(ROOT / "out" / "workbench"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    problems: list[str] = []
    work = Path(tempfile.mkdtemp(prefix="vp-wb-"))

    fs_r = _probe_fs(work)
    shell_r = _probe_shell(work)
    plan_r = _probe_plan()
    gates_r = _probe_shell_gates()

    if not fs_r.get("ok"):
        problems.append(f"fs.write 未真落盘：{fs_r.get('error') or fs_r}")
    if not shell_r.get("ok"):
        problems.append(f"shell.run 未真执行：{shell_r.get('error')}（{shell_r.get('detail', '')}）")
    if not plan_r.get("ok"):
        problems.append(f"plan 门未验证：{plan_r.get('error') or plan_r}")
    if not gates_r.get("ok"):
        problems.append(f"shell.run 闸门配置不对：{gates_r}")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    record = {"fs_write": fs_r, "shell_run": shell_r, "plan_gate": plan_r,
              "shell_gates": gates_r, "problems": problems}
    (out_dir / "smoke.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if args.json:
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 0 if not problems else 1

    setup_stdout()
    print("A12 · 工作台两只手（`38`）")
    print("-" * 78)

    def _row(name: str, r: dict[str, object]) -> None:
        mark = "OK  " if r.get("ok") else "FAIL"
        detail = r.get("error") or r.get("detail") or r.get("reason") or f"{r.get('bytes_written', '')} 字节"
        print(f"  {mark} {name:12s} {detail}")

    _row("fs.write", fs_r)
    _row("shell.run", shell_r)
    _row("plan 门", plan_r)
    _row("五道闸", gates_r)
    print("-" * 78)
    for p in problems:
        print(f"⚠ {p}")
    print(f"\n证据：{out_dir / 'smoke.json'}")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
