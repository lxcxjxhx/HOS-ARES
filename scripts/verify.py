"""一条命令跑完所有闸门（B200）——本地与 CI 共用同一份清单。

为什么需要它：这些闸门（mypy / 五道 lint / 账本 / 计划结构 / 测试）一直是
**谁记得谁跑**。人手跑意味着两件事同时发生：漏跑，以及"CI 跑的和本地跑的不是同一串命令"。
把它写成脚本之后，`.github/workflows/verify.yml` 与本地 `python scripts/verify.py`
调用的是同一份定义——清单只有一处。

退出码（与 doctor 的三档口径一致，不许"没跑也算过"）：

```text
0  全部通过
1  有失败
2  没有失败，但有跳过（未全绿：缺条件的环境不算通过）
```

用法：

```bash
python scripts/verify.py                 # 快闸门（约 1 分钟）
python scripts/verify.py --list          # 看清单
python scripts/verify.py --with-tests    # 追加分段 pytest（全量）
python scripts/verify.py --only mypy,lint-noroute
```
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

#: 控制台不一定是 UTF-8（GitHub 的 Windows runner 是 cp1252）：先钉死编码，
#: 否则打印中文本身就是一次崩溃（CI 实跑抓到）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):  # pragma: no cover - 老 Python/被重定向
        pass

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable

MYPP_TARGETS = ("veripen_core/", "worker/", "veripen_oracles/", "veripen/", "veripen_cli/", "web/")


@dataclass(frozen=True)
class Gate:
    """一道闸门：名字 + 命令 + 需要什么外部条件。"""

    name: str
    argv: tuple[str, ...]
    needs: str = ""          # "node" 表示依赖外部程序；缺了记 SKIP（不记通过）
    kind: str = "gate"       # gate=闸门 / test=测试段


def _py(*args: str) -> tuple[str, ...]:
    return (PY, *args)


def _mypy_argv() -> tuple[str, ...]:
    """mypy 命令。**不按平台裁剪**：CI 就是为了看两边是否一致（B200）。"""
    return (PY, "-m", "mypy", "--strict", *MYPP_TARGETS)


#: 闸门清单。**加一道闸门就加一行**——CI 与本地都从这里读。
GATES: tuple[Gate, ...] = (
    Gate("mypy", _mypy_argv()),
    Gate("lint-noroute", _py("scripts/lint/noroute.py")),
    Gate("lint-nodata", _py("scripts/lint/nodata_stub.py")),
    Gate("lint-ui", _py("scripts/lint/ui_zero_address.py")),
    Gate("lint-netimports", _py("scripts/lint/netimports.py")),
    Gate("lint-noleak", _py("scripts/lint/noleak.py")),
    Gate("spec-drift", _py("scripts/lint/spec_drift.py")),
    Gate("ledger", _py("scripts/lint/ledger_check.py")),
    Gate("plan", ("node", "plan-hos-ares/check-plan.cjs"), needs="node"),
)

#: 测试分三段跑：本机单进程全量约 30 分钟，超过一条命令的观察窗口。
TEST_CHUNKS: tuple[Gate, ...] = (
    Gate("tests-unit", _py("-m", "pytest", "tests/unit", "-q"), kind="test"),
    Gate("tests-integration", _py("-m", "pytest", "tests/integration", "-q"), kind="test"),
    Gate("tests-rest", _py("-m", "pytest", "tests/routes", "tests/lab", "tests/fixtures", "-q"),
         kind="test"),
)

PASS, FAIL, SKIP = "ok", "fail", "skip"


def available(gate: Gate) -> bool:
    """外部条件在不在。缺了就 SKIP——**不冒充通过**。"""
    return not gate.needs or shutil.which(gate.needs) is not None


def run_gate(gate: Gate, *, runner=None) -> tuple[str, str]:
    """跑一道闸门，返回 (状态, 摘要)。runner 可注入，便于自测。"""
    if not available(gate):
        return SKIP, f"缺 {gate.needs}（本机没装，记 skip）"
    call = runner or _default_runner
    code, tail = call(gate.argv)
    return (PASS if code == 0 else FAIL), tail


def _default_runner(argv: Sequence[str]) -> tuple[int, str]:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(  # noqa: S603 - 命令来自上面的常量清单
        list(argv), cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    lines = [ln for ln in out.strip().splitlines() if ln.strip()]
    if not lines:
        return proc.returncode, ""
    if proc.returncode == 0:
        return proc.returncode, lines[-1][:160]
    # 失败时末行往往只是汇总（"Found 12 errors"）：前几行才是可行动的信息
    head = [ln[:200] for ln in lines[:6]]
    return proc.returncode, " ⏎ ".join(head)


def select(only: str, with_tests: bool) -> list[Gate]:
    pool = list(GATES) + (list(TEST_CHUNKS) if with_tests else [])
    if not only:
        return pool
    wanted = {x.strip() for x in only.split(",") if x.strip()}
    unknown = wanted - {g.name for g in pool}
    if unknown:
        raise SystemExit(f"未知闸门：{sorted(unknown)}（用 --list 看清单）")
    return [g for g in pool if g.name in wanted]


def main(argv: list[str] | None = None, *, runner=None) -> int:
    ap = argparse.ArgumentParser(prog="verify", description="一条命令跑完所有闸门")
    ap.add_argument("--list", action="store_true", help="只打印清单")
    ap.add_argument("--with-tests", action="store_true", help="追加分段 pytest")
    ap.add_argument("--only", default="", help="只跑这几道（逗号分隔）")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    gates = select(args.only, args.with_tests)
    if args.list:
        for g in gates:
            need = f"  (需要 {g.needs})" if g.needs else ""
            print(f"{g.name:<18}{g.kind:<6}{' '.join(g.argv)}{need}")
        return 0

    results: list[tuple[Gate, str, str]] = []
    for g in gates:
        print(f"[{g.name}] 跑…", flush=True)
        status, note = run_gate(g, runner=runner)
        results.append((g, status, note))
        print(f"[{g.name}] {status}" + (f" · {note}" if note else ""), flush=True)

    failed = [r for r in results if r[1] == FAIL]
    skipped = [r for r in results if r[1] == SKIP]
    print()
    print(f"合计 {len(results)} 项：ok {len(results) - len(failed) - len(skipped)} / "
          f"fail {len(failed)} / skip {len(skipped)}")
    for g, _s, note in failed:
        print(f"  fail · {g.name} · {note}")
    if failed:
        print("结论：有闸门没过")
        return 1
    if skipped:
        print("结论：未全绿（有跳过项，缺条件的环境不算通过）")
        return 2
    print("结论：全绿")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
