"""B13 · doctor：部署自检（A3「内网能装」、`13 §3`「doctor 有任何红项不交付」）。

设计只有一条：**每一项都要能被别人独立重跑**。
所以每项是一个 ``check()``，返回 ``CheckResult``，说清「做了什么、期望什么、实际什么」；
doctor 不"综合判断"，它只汇总，判定权在人。

三档状态：
- ``ok``   —— 跑了且通过；
- ``fail`` —— 跑了但不通过（**交付前必须清零**）；
- ``skip`` —— 环境不具备（例如没有 docker、Windows 上建不了符号链接）。
  **skip 不算 ok**：把"没检查"记成"通过"，是这类工具最常见的自欺。
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Sequence


class Status(str, Enum):
    OK = "ok"
    FAIL = "fail"
    SKIP = "skip"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    detail: str = ""
    #: 建议的修复命令；fail 时必须给（否则"知道了但不改"）
    fix: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status.value,
                "detail": self.detail, "fix": self.fix}


@dataclass
class DoctorReport:
    results: list[CheckResult] = field(default_factory=list)

    @property
    def failed(self) -> list[CheckResult]:
        return [r for r in self.results if r.status is Status.FAIL]

    @property
    def skipped(self) -> list[CheckResult]:
        return [r for r in self.results if r.status is Status.SKIP]

    @property
    def green(self) -> bool:
        """**只有全 ok 才叫绿。skip 也算不绿**——没检查过的不算通过。"""
        return not self.failed and not self.skipped

    def by_status(self) -> dict[str, int]:
        return {s.value: sum(1 for r in self.results if r.status is s) for s in Status}

    def render(self) -> str:
        lines = ["# doctor", ""]
        for r in self.results:
            mark = {"ok": "✅", "fail": "❌", "skip": "⏭"}[r.status.value]
            lines.append(f"{mark} {r.name}" + (f" —— {r.detail}" if r.detail else ""))
            if r.status is Status.FAIL and r.fix:
                lines.append(f"   修复：{r.fix}")
        counts = self.by_status()
        lines += [
            "",
            f"ok {counts['ok']} / fail {counts['fail']} / skip {counts['skip']}"
            f"  → {'全绿' if self.green else '不可交付'}",
        ]
        return "\n".join(lines) + "\n"


# ══════════════════════════════════════════════════════════════════
# 单项检查
# ══════════════════════════════════════════════════════════════════
def check_python(root: Path) -> CheckResult:
    del root  # 签名统一成 (root)，方便注册表按名字遍历
    major, minor = sys.version_info[:2]
    ok = (major, minor) >= (3, 11)
    return CheckResult(
        "python 版本", Status.OK if ok else Status.FAIL,
        f"{major}.{minor}", "" if ok else "需要 ≥3.11",
    )


def check_layout(root: Path) -> CheckResult:
    required = "veripen_core veripen_oracles config tests Makefile".split()
    missing = [p for p in required if not (root / p).exists()]
    return CheckResult(
        "目录布局", Status.OK if not missing else Status.FAIL,
        f"缺 {missing}" if missing else "齐全",
        "恢复仓库结构" if missing else "",
    )


def check_tables(root: Path) -> CheckResult:
    from veripen_core.store import Store

    store = Store(":memory:").init()
    try:
        tables = store.table_names()
    finally:
        store.close()
    expected = set(
        "tasks findings capsules blackboard_facts asset_facts task_events leads "
        "routesets route_bindings approvals bench_runs".split()
    )
    missing = sorted(expected - tables)
    return CheckResult(
        "数据库表", Status.OK if not missing else Status.FAIL,
        f"{len(tables)} 张" + (f"，缺 {missing}" if missing else ""),
        "跑 store.init() 补建" if missing else "",
    )


def check_selfcheck(root: Path) -> CheckResult:
    """17 条数据一致性自检（03 §7）。**必须全部跑得通**，不是"能 import"。"""
    from veripen_core.selfcheck import run_all
    from veripen_core.store import Store

    store = Store(":memory:").init()
    try:
        outcomes = run_all(store)
    finally:
        store.close()
    bad = [o for o in outcomes if not o.ok]
    return CheckResult(
        "数据自检 17 条", Status.OK if len(outcomes) >= 17 and not bad else Status.FAIL,
        f"{len(outcomes)} 条，红 {len(bad)}",
        "看 selfcheck 失败行的违规 id" if bad else "",
    )


def _run(cmd: Sequence[str], *, cwd: Path, timeout: int = 180) -> tuple[int, str]:
    proc = subprocess.run(
        list(cmd), cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _lint(name: str, script: str, root: Path) -> CheckResult:
    path = root / "scripts" / "lint" / script
    if not path.exists():
        return CheckResult(name, Status.SKIP, f"缺 {path.relative_to(root).as_posix()}")
    try:
        code, out = _run([sys.executable, str(path)], cwd=root)
    except (subprocess.SubprocessError, OSError) as exc:
        return CheckResult(name, Status.FAIL, f"执行失败：{exc}", f"python {script}")
    tail = out.splitlines()[-1] if out else ""
    return CheckResult(
        name,
        Status.OK if code == 0 else Status.FAIL,
        tail[:160],
        "" if code == 0 else f"python scripts/lint/{script}",
    )


def check_bindings(root: Path) -> CheckResult:
    return _lint("op 绑定覆盖", "bindings_check.py", root)


def check_noroute(root: Path) -> CheckResult:
    return _lint("路由卫生（零地址）", "noroute.py", root)


def check_nodata(root: Path) -> CheckResult:
    # 显示名里不能出现禁用词本身（lint 会扫到这一行），所以叫「数据真实性」
    return _lint("数据真实性（无桩数据/硬编码）", "nodata_stub.py", root)


def check_netimports(root: Path) -> CheckResult:
    return _lint("契约层无网络依赖", "netimports.py", root)


def check_policy(root: Path) -> CheckResult:
    return _lint("默认关闭策略", "policy_check.py", root)


def check_spec_drift(root: Path) -> CheckResult:
    return _lint("规划 ↔ 代码对账", "spec_drift.py", root)


def check_ledger(root: Path) -> CheckResult:
    return _lint("批次账本诚实性", "ledger_check.py", root)


def check_ui(root: Path) -> CheckResult:
    return _lint("控制台零地址", "ui_zero_address.py", root)


def check_act_cmd(root: Path) -> CheckResult:
    return _lint("act 命令零地址", "act_cmd.py", root)


def check_profiles(root: Path) -> CheckResult:
    """双客户档必须在（A1「能卖第二家」的物证）。"""
    prof = root / "profiles"
    if not prof.exists():
        return CheckResult("客户档", Status.FAIL, "profiles/ 不存在", "恢复 profiles/")
    names = sorted(p.name for p in prof.iterdir() if p.is_dir())
    ok = len(names) >= 2
    return CheckResult(
        "客户档", Status.OK if ok else Status.FAIL,
        f"{len(names)} 个：{names}",
        # 提示里不点名具体客户档：内核不许出现客户串（I3），
        # 连"至少要哪两个"都不该由代码规定——档名是部署侧的事。
        "" if ok else "至少需要两档（新增一份 profiles/<name>/profile.yaml）",
    )


def check_lock_snapshot(root: Path) -> CheckResult:
    """插件版本锁快照可生成（B12.11）：可复现的最后一道锁。"""
    from veripen_core.plugins import Registry

    snap = Registry().snapshot_lock()
    has = len(snap.get("snapshot_sha256", "")) == 64
    return CheckResult(
        "插件锁快照", Status.OK if has else Status.FAIL,
        f"sha={snap['snapshot_sha256'][:12]}" if has else "快照为空",
        "实现 Registry.snapshot_lock" if not has else "",
    )


def check_docker(root: Path) -> CheckResult:
    """docker 存在性。**没有就是 skip，不是 fail**——本机开发不需要它。"""
    try:
        code, out = _run(["docker", "version", "--format", "{{.Server.Version}}"], cwd=root, timeout=20)
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return CheckResult("docker 运行时", Status.SKIP, "本机无 docker（离线包校验跳过）")
    return CheckResult(
        "docker 运行时", Status.OK if code == 0 else Status.SKIP,
        out.splitlines()[0][:60] if out else "无响应",
    )


# ── B61：三项运行时探测 ─────────────────────────────────────────
# 判据来源复用既有实现，不另写一套探测逻辑：两处口径一旦分叉，
# doctor 说"环境坏了"、parity 说"只是没验"，人就不知道该信谁。


def check_sandbox(root: Path) -> CheckResult:
    """沙箱执行体（B45/B61）。**没有运行时 = skip**，真隔离待运行时补验。"""
    try:
        from veripen_core.sandbox.runner import select_executor
    except Exception as exc:  # noqa: BLE001
        return CheckResult("沙箱执行体", Status.FAIL, f"导入失败：{exc}"[:120],
                           "检查 veripen_core/sandbox/runner.py")
    exe = select_executor()
    if exe is None:
        return CheckResult(
            "沙箱执行体", Status.SKIP,
            "无 docker/podman：BLOCKED 语义已验，真隔离未验",
            "装 docker 或 podman 后重跑 make exec-isolation",
        )
    name = getattr(exe, "name", type(exe).__name__)
    # 三态如实：docker=container / job-object=受限执行（不是容器）/ 无
    if name == "docker":
        return CheckResult("沙箱执行体", Status.OK, "docker（isolation: container）")
    return CheckResult(
        "沙箱执行体", Status.OK,
        f"{name}（isolation: job-object —— 资源隔离，非容器；E1 真隔离仍未验）",
    )


def check_tui(root: Path) -> CheckResult:
    """TUI（B56）。缺 curses 意味着只能走非交互降级路径。"""
    try:
        import curses  # noqa: F401, PLC0415
    except Exception:  # noqa: BLE001
        return CheckResult("TUI", Status.SKIP, "无 curses（Windows 需 windows-curses）",
                           "pip install windows-curses")
    return CheckResult("TUI", Status.OK, "curses 可用")


def check_browser(root: Path) -> CheckResult:
    """浏览器渲染执行体（B46）。playwright 缺失时渲染走 blocked，不是错。"""
    try:
        from veripen_core.render.browser import BrowserRenderer
    except Exception as exc:  # noqa: BLE001
        return CheckResult("浏览器渲染", Status.FAIL, f"导入失败：{exc}"[:120],
                           "检查 veripen_core/render/browser.py")
    if not BrowserRenderer().available():
        return CheckResult("浏览器渲染", Status.SKIP, "playwright 不可用：渲染走 blocked",
                           "pip install playwright && playwright install chromium")
    return CheckResult("浏览器渲染", Status.OK, "playwright 可用")


#: doctor 的 19 项（顺序即渲染顺序）。**加一项就要问：它能不能被别人独立重跑？**
CHECKS: tuple[tuple[str, Callable[[Path], CheckResult]], ...] = (
    ("python", check_python),
    ("layout", check_layout),
    ("tables", check_tables),
    ("selfcheck", check_selfcheck),
    ("bindings", check_bindings),
    ("noroute", check_noroute),
    ("nodata", check_nodata),
    ("netimports", check_netimports),
    ("policy", check_policy),
    ("spec_drift", check_spec_drift),
    ("ledger", check_ledger),
    ("ui", check_ui),
    ("act_cmd", check_act_cmd),
    ("profiles", check_profiles),
    ("lock_snapshot", check_lock_snapshot),
    ("docker", check_docker),
    ("sandbox", check_sandbox),
    ("tui", check_tui),
    ("browser", check_browser),
)


def run_doctor(root: str | Path, *, only: Sequence[str] = ()) -> DoctorReport:
    base = Path(root).resolve()
    report = DoctorReport()
    for name, fn in CHECKS:
        if only and name not in only:
            continue
        try:
            report.results.append(fn(base))
        except Exception as exc:  # 单项崩溃不能拖垮整份报告
            report.results.append(
                CheckResult(name, Status.FAIL, f"检查自身抛错：{type(exc).__name__}: {exc}"[:160],
                            "修该项检查或补依赖")
            )
    return report


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="部署自检")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--only", nargs="*", default=[])
    ap.add_argument("--json", action="store_true")
    # B61：``python -m`` 调用时 argv 为 None，原实现传空列表 → 所有参数被静默忽略
    args = ap.parse_args(sys.argv[1:] if argv is None else list(argv))

    report = run_doctor(args.root, only=args.only)
    # Windows 控制台是 GBK：统一按 UTF-8 写出，避免 ✅/❌/替换字符触发 UnicodeEncodeError。
    # 用 getattr 兜一下：mypy 认为 sys.stdout 可能是任意 TextIO（无 reconfigure）。
    # B61：两个分支都要，``--json`` 此前因 argv 被忽略而从未真正执行过。
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="replace")
    if args.json:
        print(json.dumps({"results": [r.as_dict() for r in report.results],
                          "green": report.green, "counts": report.by_status()},
                         ensure_ascii=False, indent=2))
    else:
        print(report.render())
    return 0 if report.green else 1


if __name__ == "__main__":
    raise SystemExit(main())
