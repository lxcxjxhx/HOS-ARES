#!/usr/bin/env python3
"""B223 · 交付材料里那几条命令的**跨平台入口**（Windows 上没有 make）。

为什么要有它：交付材料（`docs/delivery/quickref.md` / `dist-checklist.md`）通篇是
`make doctor` / `make demo` / `make bench`——而**我们同时发 Windows 的 exe 与安装包**，
那台机器上往往没有 `make`。文档里的第一步就走不通，是一类最难被自己发现的缺陷：
写文档的人在装了 make 的机器上，读文档的人在没装的机器上。

用法（与 Makefile 同名同义）：

```
python scripts/tasks.py doctor
python scripts/tasks.py lint
python scripts/tasks.py demo
python scripts/tasks.py demo-alt
python scripts/tasks.py demo-offline
python scripts/tasks.py bench
python scripts/tasks.py verify-report
python scripts/tasks.py replay-offline --capsule out/demo/capsules
python scripts/tasks.py exec-isolation
python scripts/tasks.py console          # 起只读控制台（原先文档里的 `make up` 并不存在）
python scripts/tasks.py --list
```

三条纪律：

1. **只做转发，不重实现**：每个任务就是 Makefile 那行命令的等价物，args 原样透传；
2. **不假装有 make**：装了就提示"两条路等价"，不会自己再发明第三套参数；
3. **清单只有一处**：任务表和 `docs/delivery` 里出现的命令由测试对齐（见
   `tests/unit/test_tasks_runner.py`）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: 任务名 → 命令行（与 Makefile 同名目标等价）
TASKS: dict[str, tuple[str, ...]] = {
    "doctor": ("-m", "veripen_cli.cmd_doctor"),
    # `make lint` = bindings-check + verify 里的七道 lint（B208）
    "lint": ("scripts/verify.py", "--only",
             "lint-noroute,lint-nodata,lint-ui,lint-netimports,lint-noleak,lint-lock,spec-drift"),
    "verify": ("scripts/verify.py",),
    "verify-all": ("scripts/verify.py", "--with-tests"),
    "demo": ("scripts/demo.py",),
    "demo-alt": ("scripts/demo.py", "--profile", "demo-b", "--alt-profile", "demo-a",
                 "--out", "out/demo-alt"),
    "demo-offline": ("scripts/demo.py", "--out", "out/demo", "--offline-only"),
    "bench": ("scripts/b12/run_bench.py", "--rounds", "3", "--out", "out/b12"),
    "verify-report": ("scripts/b11/verify_manifest.py", "out/b11/report"),
    "replay-offline": ("-m", "veripen_core.evidence", "--offline-fixture"),
    "exec-isolation": ("-m", "pytest", "tests/unit/test_sandbox_policy.py",
                       "tests/integration/test_sandbox_isolation.py", "-q"),
    "console": ("-m", "veripen_cli.cmd_console"),
}


def _python() -> str:
    return sys.executable or "python"


def run(task: str, rest: list[str]) -> int:
    argv = TASKS[task]
    if task == "bench":
        # 与 `make bench` 同义；`--rounds/--limit` 这类额外参数可继续透传
        cmd = [_python(), *argv, *rest]
    else:
        cmd = [_python(), *argv, *rest]
    print(f"[tasks] {' '.join(cmd)}", flush=True)
    return subprocess.call(cmd, cwd=str(ROOT))  # noqa: S603 - 固定 argv，无 shell


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "--list", "help"):
        width = max(len(n) for n in TASKS)
        print("python scripts/tasks.py <任务> [参数…]")
        print("")
        for name in sorted(TASKS):
            print(f"  {name:<{width}}  {' '.join(TASKS[name])}")
        print("")
        print("等价于 Makefile 的同名目标；Windows 没有 make 时用这个。")
        return 0
    task, rest = args[0], args[1:]
    if task not in TASKS:
        print(f"未知任务：{task}（--list 看全部）", file=sys.stderr)
        return 2
    return run(task, rest)


if __name__ == "__main__":
    raise SystemExit(main())
