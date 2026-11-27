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
from typing import Any, Callable, Sequence

#: 控制台不一定是 UTF-8（GitHub 的 Windows runner 是 cp1252）：先钉死编码，
#: 否则打印中文本身就是一次崩溃（CI 实跑抓到）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):  # pragma: no cover - 老 Python/被重定向
        pass

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable

#: mypy 的扫描目标。**`scripts/` 是 B576 加进来的**：
#:
#: 它以前不在这里，于是 `scripts/` 里躺了 74 个类型错误而 verify 一直绿
#: （B570 量化 → B571–B574 逐个修到 0 → B576 并入）。
#:
#: **`tests/unit` 不在这个列表里**，它单独一道闸门（`mypy-tests`）：
#: 与 `scripts/` 同轮扫描会撞「`scripts/verify.py` 被解析成 `verify` 与
#: `scripts.verify` 两个模块名」——测试里 `import verify`，两条路径同时可达。
#: 拆成两道闸门后两边各自全绿（B599 实测）。
MYPP_TARGETS = (
    "hos_ares_core/", "worker/", "hos_ares_oracles/", "hos_ares/", "hos_ares_cli/",
    "web/", "scripts/",
)

#: mypy 的排除规则（正则）。**两个文件，同一类配置歧义**：
#:
#: 1. `test_hint_commands_exist` → 被解析成 `test_x` 与 `tests.unit.test_x`；
#: 2. `tests/unit/conftest` → 被解析成 `conftest` 与 `unit.conftest`。
#:
#: 起因都是 `mypy_path` 同时含 `tests` 与 `tests/unit`（两条路径都到达同一个文件）。
#: 它们会让 mypy **中止整轮检查**（不是报一个错），后面 180+ 文件一个都不扫。
#:
#: 试过四种方式都不解：`--explicit-package-bases`、换工作目录、
#: 改 `mypy_path`（会把 `scripts/` 里的 `capsule_fixture` import 弄坏）、
#: 加 `tests/unit/__init__.py`（把问题转移到 `capsule_fixture.py`）。
#:
#: **代价可接受**：两个文件单独扫都零错误。排除它们不影响覆盖，只是不参与闸门。
MYPY_EXCLUDE = ("test_hint_commands_exist", "tests/unit/conftest")


@dataclass(frozen=True)
class Gate:
    """一道闸门：名字 + 命令 + 需要什么外部条件。"""

    name: str
    argv: tuple[str, ...]
    needs: str = ""          # "node" 表示依赖外部程序；缺了记 SKIP（不记通过）
    kind: str = "gate"       # gate=闸门 / test=测试段
    cwd: str = ""            # 相对仓库根的工作目录（有的校验器只认自己那层）


def _py(*args: str) -> tuple[str, ...]:
    return (PY, *args)


def _mypy_argv() -> tuple[str, ...]:
    """mypy 命令（产品 + 脚本）。**不按平台裁剪**：CI 就是为了看两边是否一致（B200）。

    这里**不含** `tests/unit`：把它和 `scripts/` 放同一轮扫描会撞
    「`scripts/verify.py` 被解析成 `verify` 与 `scripts.verify` 两个模块名」——
    因为测试里 `import verify`，两条路径同时可达（B599 实测）。
    测试单独一道闸门（见 `_mypy_tests_argv`）。
    """
    return (PY, "-m", "mypy", "--strict", *MYPP_TARGETS)


def _mypy_tests_argv() -> tuple[str, ...]:
    """测试目录的类型检查。**单独一道闸门**，理由见上。

    `--exclude` 排掉 `test_hint_commands_exist`：它在 `mypy_path` 含 `tests` 时
    会被解析成两个模块名，mypy 会**中止整轮**（不是报一个错）。排掉后 184 个文件照扫。
    """
    exclude: list[str] = []
    for pattern in MYPY_EXCLUDE:
        exclude += ["--exclude", pattern]
    return (PY, "-m", "mypy", "--strict", *exclude, "tests/unit")


#: 闸门清单。**加一道闸门就加一行**——CI 与本地都从这里读。
GATES: tuple[Gate, ...] = (
    Gate("mypy", _mypy_argv()),
    # 测试目录单独一道：与产品/脚本同轮扫会撞模块名歧义（见函数 docstring）
    Gate("mypy-tests", _mypy_tests_argv()),
    Gate("lint-noroute", _py("scripts/lint/noroute.py")),
    Gate("lint-nodata", _py("scripts/lint/nodata_stub.py")),
    Gate("lint-ui", _py("scripts/lint/ui_zero_address.py")),
    Gate("lint-netimports", _py("scripts/lint/netimports.py")),
    Gate("lint-noleak", _py("scripts/lint/noleak.py")),
    # 锁与 pyproject 是同一份事实的两个出口：漂了，内网离线装就会少包（B201）
    Gate("lint-lock", _py("scripts/lint/lock_sync.py")),
    Gate("spec-drift", _py("scripts/lint/spec_drift.py")),
    Gate("ledger", _py("scripts/lint/ledger_check.py")),
    Gate("plan", ("node", "plan-hos-ares/check-plan.cjs"), needs="node"),
    # 冻结包**自带**的内容寻址闸门。`ae04456` 改过 `06-证据与replay规格.md`
    # 却没重生成它的 `MANIFEST.md`，于是那 54 个文件的哈希表过期了——一直没人
    # 知道，因为这行不在清单里（B232）。两个包各有各的 MANIFEST，得各跑一遍。
    Gate("plan-old-manifest", ("node", "_verify-manifest.cjs"),
         needs="node", cwd="plan-hos-legacy"),
)

#: 测试分三段跑：本机单进程全量约 30 分钟，超过一条命令的观察窗口。
#: 冒烟类闸门：只证明链路能跑通，**不产出验收数字**（子集跑）
SMOKES: tuple[Gate, ...] = (
    Gate("bench-smoke", _py("scripts/b12/run_bench.py", "--rounds", "1",
                            "--limit", "3", "--out", "out/bench-smoke"), kind="smoke"),
)

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


def run_gate(gate: Gate, *, runner: Callable[..., Any] | None = None,
             name: str = "") -> tuple[str, str]:
    """跑一道闸门，返回 (状态, 摘要)。runner 可注入，便于自测。"""
    if not available(gate):
        return SKIP, f"缺 {gate.needs}（本机没装，记 skip）"
    call = runner or (lambda argv: _default_runner(argv, name or gate.name, cwd=gate.cwd))
    code, tail = call(gate.argv)
    return (PASS if code == 0 else FAIL), tail


#: 每道闸门的完整输出落在这里：CI 日志会被截断，文件不会（B200）
LOG_DIR = ROOT / "out" / "verify"


def _dump(name: str, text: str) -> str:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"{name}.log"
    path.write_text(text, encoding="utf-8", errors="replace")
    return path.relative_to(ROOT).as_posix()


def _default_runner(argv: Sequence[str], name: str = "gate", *, cwd: str = "") -> tuple[int, str]:
    # PYTHONUNBUFFERED（B425）：闸门里跑的是 pytest，输出进管道时默认块缓冲——
    # 长跑时日志几十分钟不变，看的人会把它当"挂死"（B424 就是这么误判两轮的）。
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    where = str(ROOT / cwd) if cwd else str(ROOT)
    proc = subprocess.run(  # noqa: S603 - 命令来自上面的常量清单
        list(argv), cwd=where, capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    saved = _dump(name, out)
    lines = [ln for ln in out.strip().splitlines() if ln.strip()]
    if not lines:
        return proc.returncode, ""
    if proc.returncode == 0:
        return proc.returncode, lines[-1][:160]
    # 失败时：**头尾都要**。头几行常是第一条错因，尾部才是 FAILED 列表与断言；
    # 只给头（或只给尾）都会让 CI 日志失去可行动性（B200 已踩一次）。
    head = [ln[:160] for ln in lines[:2]]
    tail = [ln[:160] for ln in lines[-10:]]
    return proc.returncode, " ⏎ ".join(head + ["…"] + tail + [f"完整输出：{saved}"])


def select(only: str, with_tests: bool, quick_tests: bool = False) -> list[Gate]:
    # B445：`--quick-tests` 只跑单测（约 2 分钟）。全量 `--with-tests` 本机要一小时，
    # 于是「改了 pyproject 之后该跑什么」这个问题，实际答案成了「什么也不跑」——
    # B439 就是这么红了三条打包用例而没人发现的。
    extra = list(TEST_CHUNKS) if with_tests else ([TEST_CHUNKS[0]] if quick_tests else [])
    pool = list(GATES) + list(SMOKES) + extra
    if not only:
        return pool
    wanted = {x.strip() for x in only.split(",") if x.strip()}
    unknown = wanted - {g.name for g in pool}
    if unknown:
        raise SystemExit(f"未知闸门：{sorted(unknown)}（用 --list 看清单）")
    return [g for g in pool if g.name in wanted]


def interpreter_notes(exe: str, root: Path) -> list[str]:
    """报解释器；仓库自带 `.venv` 而没用它时再补一句（B367）。

    本地同时存在两个环境时，"一堆红"很可能只是这个 python 里没装 playwright/mypy。
    日志里先看得见这一点，读者才不会跑去改代码。
    """
    notes = [f"python: {exe}"]
    venv = root / ".venv"
    try:
        inside = Path(exe).resolve().is_relative_to(venv.resolve())
    except (OSError, ValueError):  # 路径不存在的怪情况：不当成"在里面"
        inside = False
    if venv.exists() and not inside:
        notes.append(f"提示：仓库自带 {venv.as_posix()}{chr(0x2F)}，"
                     "当前用的不是它（依赖可能不全）")
    return notes


def main(argv: list[str] | None = None, *,
         runner: Callable[..., Any] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="verify", description="一条命令跑完所有闸门")
    ap.add_argument("--list", action="store_true", help="只打印清单")
    ap.add_argument("--with-tests", action="store_true", help="追加分段 pytest")
    ap.add_argument("--quick-tests", action="store_true", help="只追加单测（约 2 分钟）")
    ap.add_argument("--only", default="", help="只跑这几道（逗号分隔）")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    gates = select(args.only, args.with_tests, args.quick_tests)
    for line in interpreter_notes(sys.executable, ROOT):
        print(line)
    if args.list:
        for g in gates:
            need = f"  (需要 {g.needs})" if g.needs else ""
            print(f"{g.name:<18}{g.kind:<6}{' '.join(g.argv)}{need}")
        return 0

    results: list[tuple[Gate, str, str]] = []
    for g in gates:
        print(f"[{g.name}] 跑…", flush=True)
        status, note = run_gate(g, runner=runner, name=g.name)
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
