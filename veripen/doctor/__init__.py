"""B13 交付硬化：doctor 16 项 + 死信 + 3 worker 并行不重不漏。

doctor 的设计取向：**每项都能自己跑、失败要指出"该改哪里"**，
不是打印一串绿勾。任何一项不过就 exit 1，不给"看着还行"的余地。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Sequence

#: 仓库根（doctor 要跑仓库根的 scripts/ 与 tests/）
ROOT = Path(__file__).resolve().parents[2]


class Level(str, Enum):
    OK = "ok"
    WARN = "warn"   # 不阻断，但要在报告里写
    FAIL = "fail"   # 阻断


@dataclass(frozen=True)
class Check:
    name: str
    level: Level
    detail: str
    fix: str = ""  # 失败时"该改哪里"——不许只说"失败"

    def line(self) -> str:
        mark = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}[self.level.value]
        tail = f"  → {self.fix}" if self.fix and self.level is not Level.OK else ""
        return f"{mark} {self.name}: {self.detail}{tail}"


@dataclass
class DoctorReport:
    checks: list[Check] = field(default_factory=list)

    def add(self, check: Check) -> None:
        self.checks.append(check)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.level is Level.FAIL]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.level is Level.WARN]

    def as_text(self) -> str:
        return "\n".join(c.line() for c in self.checks)

    def to_json(self) -> dict[str, object]:
        return {
            "schema": "veripen.doctor/v1",
            "ok": not self.failures,
            "counts": {
                "ok": sum(1 for c in self.checks if c.level is Level.OK),
                "warn": len(self.warnings),
                "fail": len(self.failures),
            },
            "checks": [
                {"name": c.name, "level": c.level.value, "detail": c.detail, "fix": c.fix}
                for c in self.checks
            ],
        }


def _run(cmd: Sequence[str], *, timeout: int = 120) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            list(cmd), capture_output=True, text=True, timeout=timeout, cwd=ROOT
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, f"{type(exc).__name__}: {exc}"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


# ══════════════════════════════════════════════════════════════════
# 16 项检查
# ══════════════════════════════════════════════════════════════════
def _check_python() -> Check:
    v = sys.version_info
    ok = (v.major, v.minor) >= (3, 11)
    return Check("python", Level.OK if ok else Level.FAIL, f"{v.major}.{v.minor}",
                 fix="" if ok else "需要 Python >= 3.11")


def _check_deps() -> Check:
    missing = []
    for mod in ("pydantic", "yaml"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    return Check("deps", Level.OK if not missing else Level.FAIL,
                 "pydantic + yaml 就绪" if not missing else f"缺 {missing}",
                 fix="" if not missing else "pip install pydantic pyyaml")


def _check_package_imports() -> Check:
    try:
        for mod in ("veripen_core", "veripen_oracles", "veripen_contract", "veripen"):
            __import__(mod)
    except Exception as exc:  # noqa: BLE001
        return Check("imports", Level.FAIL, f"{type(exc).__name__}: {exc}",
                     fix="检查包结构与 __init__ 导出")
    return Check("imports", Level.OK, "4 个包可导入")


def _check_oracle_registry() -> Check:
    from veripen_oracles import ALL_ORACLES, TIER_A, TIER_B, TIER_C

    total = len(ALL_ORACLES)
    ok = total == 15 and len(TIER_A) == 6 and len(TIER_B) == 5 and len(TIER_C) == 3
    return Check("oracles", Level.OK if ok else Level.FAIL,
                 f"注册 {total} 类（A{len(TIER_A)} B{len(TIER_B)} C{len(TIER_C)} + auth）",
                 fix="" if ok else "注册表与 18 §0 的 14 类不一致")


def _check_default_off() -> Check:
    from veripen_oracles import ALL_ORACLES, policy

    decision = policy.check_enabled([c.meta.id for c in ALL_ORACLES])
    missing = sorted(set(policy.DEFAULT_OFF) - set(decision.blocked))
    return Check("default-off", Level.OK if not missing else Level.FAIL,
                 f"{len(policy.DEFAULT_OFF)} 类默认关闭并拦住" if not missing else f"未拦住 {missing}",
                 fix="" if not missing else "policy.DEFAULT_OFF 与启动校验不一致")


def _check_routes_deterministic() -> Check:
    # 靶场路径在 tests/ 下，仅运行期可达（mypy 静态看不到，见 pyproject 的 mypy 配置）
    import lab.routes as R  # noqa: N812

    a, b = R.routeset(), R.routeset()
    same = a.routeset_sha256 == b.routeset_sha256
    return Check("routeset", Level.OK if same else Level.FAIL,
                 f"两次解析同哈希 {a.routeset_sha256[:12]}" if same else "两次解析哈希不同",
                 fix="" if same else "resolver 有隐藏非确定性")


def _check_capsule_roundtrip() -> Check:
    import tempfile

    import capsule_fixture as F

    from veripen_core.evidence import dumps_capsule, load_capsule, save_capsule, verify_chain

    cap = F.sealed()
    with tempfile.TemporaryDirectory() as tmp:
        path = save_capsule(cap, tmp)
        again = load_capsule(path)
    same = dumps_capsule(again) == dumps_capsule(cap) and verify_chain([cap]).ok
    return Check("capsule", Level.OK if same else Level.FAIL,
                 "seal → 落盘 → 读回字节一致" if same else "读回字节不一致",
                 fix="" if same else "检查 canonical_json 是否稳定")


def _check_replay_offline() -> Check:
    import capsule_fixture as F

    from veripen_core.evidence import ReplayEnv, ReplayMode, replay
    from veripen_core.models import ReplayStatus

    result = replay(F.sealed(), ReplayEnv(now=0, mode=ReplayMode.OFFLINE))
    ok = result.state is ReplayStatus.OK
    return Check("replay-offline", Level.OK if ok else Level.FAIL,
                 f"离线 replay={result.state.value}" if ok else f"离线 replay={result.state.value}",
                 fix="" if ok else "判定层引入了 IO/时钟")


def _check_report_render() -> Check:
    from veripen.report import FindingView, ReportInput, lint_report, render_json, render_markdown, render_sarif

    f = FindingView(
        finding_id="d-1", title="doctor 自检", asset_id="web-01",
        oracle_id="idor_cross_account", cwe="CWE-639", severity="medium", confidence=0.9,
        route_strength="contract", route_ref="route://contract/" + "0" * 16,
        capsule_id="c" * 64, self_sha256="c" * 64, replay_status="OK",
        situation="可用于自检", impact="", advice="",
    )
    report = ReportInput(
        engagement="doctor", profile_hash="h" * 64, image_digest="d" * 64,
        root_sha256="0" * 64, findings=(f,), coverage={"zero_hit_tags": [], "by_source": {}},
        frameworks=[],
    )
    md, sarif, js = render_markdown(report), render_sarif(report), render_json(report)
    hits = lint_report([f], markdown=md, sarif=sarif, js=js)
    return Check("report", Level.OK if not hits else Level.FAIL,
                 "三视图渲染且零地址" if not hits else f"{len(hits)} 处地址泄漏",
                 fix="" if not hits else "检查 license/lint 白名单")


def _check_bench_baseline() -> Check:
    path = ROOT / "out" / "b12" / "bench.json"
    if not path.exists():
        return Check("bench", Level.WARN, "无基线（未跑 make bench）", fix="make bench")
    data = json.loads(path.read_text(encoding="utf-8"))
    ok = data.get("gates", {}).get("ok") is True
    fp = sum(r["fp"] for r in data.get("rounds", []))
    return Check("bench", Level.OK if ok and fp == 0 else Level.FAIL,
                 f"baseline gates ok, FP={fp}" if ok else f"baseline gates 未过 (FP={fp})",
                 fix="" if ok and fp == 0 else "先修 bench 硬门槛")


def _check_contract_parse() -> Check:
    from veripen_contract import ContractHostOutOfScope, parse_openapi, plan

    sample = (ROOT / "tests" / "fixtures" / "contract" / "sample.json").read_text(encoding="utf-8")
    opset = parse_openapi(sample, asset_id="web-01", allowed_hosts=["contract-sample.invalid"])
    a = plan(opset, confirm={"object_read"}).counts["candidates"]
    b = plan(opset, confirm={"object_read"}).counts["candidates"]
    try:
        parse_openapi(sample, asset_id="web-01", allowed_hosts=["other.invalid"])
        host_guard = False
    except ContractHostOutOfScope:
        host_guard = True
    ok = a == b and a > 0 and host_guard
    return Check("contract", Level.OK if ok else Level.FAIL,
                 f"{opset.count} op → {a} 候选，host 越界即拒" if ok else "契约解析不稳定或 host 未拦",
                 fix="" if ok else "检查 parser 的排序与 host 校验")


def _check_netimports() -> Check:
    rc, out = _run([sys.executable, "scripts/lint/netimports.py"])
    return Check("netimports", Level.OK if rc == 0 else Level.FAIL,
                 "契约/路由层零网络 import" if rc == 0 else out.strip()[:120],
                 fix="" if rc == 0 else "移除网络库 import")


def _check_noroute() -> Check:
    rc, out = _run([sys.executable, "scripts/lint/noroute.py"])
    return Check("noroute", Level.OK if rc == 0 else Level.FAIL,
                 out.strip()[:120] if rc else "零地址闸门通过",
                 fix="" if rc == 0 else "移除地址字面量或走 RouteRef")


def _check_bindings() -> Check:
    rc, out = _run([sys.executable, "scripts/lint/bindings_check.py"])
    return Check("bindings", Level.OK if rc == 0 else Level.FAIL,
                 out.strip().splitlines()[-1][:120] if out.strip() else "op 绑定齐全",
                 fix="" if rc == 0 else "补齐 config/bindings/service.yaml")


def _check_policy() -> Check:
    rc, out = _run([sys.executable, "scripts/lint/policy_check.py"])
    return Check("policy", Level.OK if rc == 0 else Level.FAIL,
                 out.strip()[:120] if rc else "默认关闭校验通过",
                 fix="" if rc == 0 else "修 DEFAULT_OFF 或顶棚约束")


def _check_disk_space() -> Check:
    """磁盘余量（12 §4：<2GB 警告）。"""
    usage = shutil.disk_usage(ROOT)
    free_gb = usage.free / (1024 ** 3)
    level = Level.OK if free_gb >= 2 else Level.WARN
    return Check("disk", level, f"{free_gb:.1f} GB 可用",
                 fix="" if level is Level.OK else "清理 out/ 或扩容（capsule 会持续增长）")


CHECKS: tuple[Callable[[], Check], ...] = (
    _check_python,
    _check_deps,
    _check_package_imports,
    _check_oracle_registry,
    _check_default_off,
    _check_routes_deterministic,
    _check_capsule_roundtrip,
    _check_replay_offline,
    _check_report_render,
    _check_bench_baseline,
    _check_contract_parse,
    _check_netimports,
    _check_noroute,
    _check_bindings,
    _check_policy,
    _check_disk_space,
)


def run_all() -> DoctorReport:
    report = DoctorReport()
    for check in CHECKS:
        try:
            report.add(check())
        except Exception as exc:  # 单项异常不拖垮整体，但要如实报
            report.add(Check(check.__name__.lstrip("_"), Level.FAIL,
                             f"{type(exc).__name__}: {exc}", fix="见 traceback 前的第一行"))
    return report


__all__ = ["CHECKS", "Check", "DoctorReport", "Level", "run_all"]
