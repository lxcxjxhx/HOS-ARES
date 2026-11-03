"""B12.12 · 装配与准入（36 §4）：**四类挂载点，只有 oracle 能碰判定**。

这张表是整个插件体系的安全边界，也是它能"开放"却不"失控"的全部原因：

| 挂载点 | 能碰判定吗 | 门槛 |
|---|---|---|
| knowledge | ❌ 永不 | 无（只读文本） |
| act | ❌ 只能产 Lead | 必须沙箱 + 二进制白名单 |
| source | ⚠️ 能影响绑定 | 人工 review + 必过 N1/N2/N3/N6 |
| oracle | ✅ 可以 | **签名 + review + 44 例 bench FP=0** |

**装 100 个 knowledge 是安全的；装 1 个 oracle 是重流程。**

本模块把这张表从"文档约定"变成"装配期拒绝"：
不是提示、不是告警、不是 UI 上不画按钮——是 **admit() 返回拒绝**。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from ..actions import BINARY_WHITELIST
from .registry import Mount, PluginRecord, Registry, RegistryError, Trust

#: 红线 P1（36 §4.3）：插件进程**永远不能直接访问目标**。
#: 它只能通过 harness 的 tools.http 或沙箱发请求。
EGRESS_POLICY = "proxied_only"

#: oracle 插件必须满足的不变量（36 §4.2）
ORACLE_INVARIANTS: frozenset[str] = frozenset({"I1", "I2", "I4", "I8"})
#: source 插件必须满足的路由不变量
SOURCE_INVARIANTS: frozenset[str] = frozenset({"N1", "N2", "N3", "N6"})
#: oracle 上 bench 的硬门槛
BENCH_FP_LIMIT = 0
BENCH_CASES = 44


class ReviewLevel(str, Enum):
    NONE = "none"
    FIRST_RUN = "first_run"
    HUMAN = "human"
    HUMAN_BENCH = "human + bench"


class AdmitError(RegistryError):
    """准入失败。**拒绝安装**，不是"警告后继续"。"""

    code = "VP-8012"


@dataclass(frozen=True)
class MountPolicy:
    """一个挂载点的准入规则（36 §4.2 逐字段落地）。"""

    mount: Mount
    allow_install: str          # true | manual | false
    sandbox: str                # none | required
    review: ReviewLevel
    must_satisfy: frozenset[str] = frozenset()
    signature_required: bool = False
    bench_required: bool = False
    binary_whitelist: frozenset[str] = frozenset()

    def as_dict(self) -> dict[str, Any]:
        return {
            "allow_install": self.allow_install,
            "sandbox": self.sandbox,
            "review": self.review.value,
            "must_satisfy": sorted(self.must_satisfy),
            "signature_required": self.signature_required,
            "bench_required": self.bench_required,
            "binary_whitelist": sorted(self.binary_whitelist),
        }


def default_policies() -> Mapping[Mount, MountPolicy]:
    return {
        Mount.KNOWLEDGE: MountPolicy(
            Mount.KNOWLEDGE, allow_install="true", sandbox="none", review=ReviewLevel.NONE,
        ),
        Mount.ACT: MountPolicy(
            Mount.ACT, allow_install="true", sandbox="required", review=ReviewLevel.FIRST_RUN,
            binary_whitelist=BINARY_WHITELIST,
        ),
        Mount.SOURCE: MountPolicy(
            Mount.SOURCE, allow_install="manual", sandbox="none", review=ReviewLevel.HUMAN,
            must_satisfy=SOURCE_INVARIANTS,
        ),
        Mount.ORACLE: MountPolicy(
            Mount.ORACLE, allow_install="manual", sandbox="none",
            review=ReviewLevel.HUMAN_BENCH, must_satisfy=ORACLE_INVARIANTS,
            signature_required=True, bench_required=True,
        ),
    }


@dataclass(frozen=True)
class Candidate:
    """待装配的插件。``manifest`` 是插件包自带的声明（**不可信，要逐项核**）。"""

    record: PluginRecord
    manifest: Mapping[str, Any] = field(default_factory=dict)
    content_sha256: str = ""
    signature: str = ""
    invariants_ok: frozenset[str] = frozenset()
    bench: Mapping[str, Any] = field(default_factory=dict)
    creates_process: bool = False
    direct_egress: bool = False
    requested_binaries: Sequence[str] = ()
    route_test_cases: int = 0


@dataclass(frozen=True)
class Admission:
    admitted: bool
    mount: Mount
    reasons: tuple[str, ...] = ()
    #: 装配时锁下的版本快照（B12.11 的 snapshot_lock），写进任务上下文
    lock_snapshot: Mapping[str, Any] = field(default_factory=dict)

    @property
    def may_judge(self) -> bool:
        return self.admitted and self.mount.may_judge


def _fail(mount: Mount, *reasons: str) -> Admission:
    return Admission(admitted=False, mount=mount, reasons=tuple(reasons))


def admit(
    cand: Candidate, *, policies: Mapping[Mount, MountPolicy] | None = None,
    bench_cases: int = BENCH_CASES,
) -> Admission:
    """准入判定。**任一不满足即拒**，并把所有拒绝理由一次列全。"""
    pol = (policies or default_policies())[cand.record.scenario]
    mount = cand.record.scenario
    reasons: list[str] = []

    if pol.allow_install == "false":
        return _fail(mount, f"{mount.value}: 策略禁止安装")

    # ── 红线 P1：插件进程不能直接访问目标 ────────────────────────
    if cand.direct_egress:
        reasons.append(
            f"{mount.value}: 插件声明直连出网（36 §4.3 红线 P1：只能走 harness 代理）"
        )

    # ── 沙箱 ────────────────────────────────────────────────────
    if pol.sandbox == "required" and not cand.creates_process:
        reasons.append(f"{mount.value}: 策略要求沙箱，但插件未声明在容器内运行")

    # ── 二进制白名单（act）──────────────────────────────────────
    if pol.binary_whitelist:
        bad = sorted(set(cand.requested_binaries) - pol.binary_whitelist)
        if bad:
            reasons.append(f"{mount.value}: 请求白名单外二进制 {bad}")

    # ── 签名（oracle）────────────────────────────────────────────
    if pol.signature_required and not cand.signature:
        reasons.append(f"{mount.value}: 缺签名清单（36 §4.2 signature: required）")

    # ── 不变量 ──────────────────────────────────────────────────
    missing = sorted(pol.must_satisfy - cand.invariants_ok)
    if missing:
        reasons.append(f"{mount.value}: 未满足不变量 {missing}")

    # ── bench（oracle）──────────────────────────────────────────
    if pol.bench_required:
        cases = int(cand.bench.get("cases") or 0)
        fp = int(cand.bench.get("fp") or 0)
        if cases < bench_cases:
            reasons.append(f"{mount.value}: bench 只跑了 {cases} 例 < {bench_cases}")
        if fp > BENCH_FP_LIMIT:
            reasons.append(f"{mount.value}: bench FP={fp} > {BENCH_FP_LIMIT}")

    # ── source 的用例数（36 §4.2 must_have）─────────────────────
    if mount is Mount.SOURCE and cand.route_test_cases < 3:
        reasons.append(f"source: tests/routes 用例 {cand.route_test_cases} < 3")

    # ── 人工 review 档位 ────────────────────────────────────────
    if pol.review in (ReviewLevel.HUMAN, ReviewLevel.HUMAN_BENCH) and cand.record.trust is Trust.AUTO:
        reasons.append(f"{mount.value}: 需要人工 review，当前 trust=auto")

    if reasons:
        return _fail(mount, *reasons)
    return Admission(admitted=True, mount=mount)


def admit_and_install(
    cand: Candidate, registry: Registry, *, now: Any,
    policies: Mapping[Mount, MountPolicy] | None = None,
) -> tuple[Admission, Mapping[str, Any]]:
    """准入通过才写清单 + 写锁，并返回**当时的 lock 快照**。

    这里用上 B12.11 的 ``snapshot_lock()``：装配那一刻的版本集合是证据的一部分，
    任务结束后上游变了，仍能按快照复现。
    """
    result = admit(cand, policies=policies)
    if not result.admitted:
        return result, {}
    registry.install(cand.record, content_sha256=cand.content_sha256, now=now)
    return (
        Admission(
            admitted=True, mount=result.mount, lock_snapshot=registry.snapshot_lock()
        ),
        registry.snapshot_lock(),
    )


def assert_only_oracle_judges(admissions: Sequence[Admission]) -> None:
    """装配后自检：**除 oracle 外，任何挂载点都不得带判定权限**。"""
    bad = [
        a.mount.value for a in admissions
        if a.may_judge and a.mount is not Mount.ORACLE
    ]
    if bad:  # pragma: no cover - 由 admit 保证，留作将来改动时的兜底
        raise AdmitError(f"非 oracle 挂载点取得判定权限：{bad}")


def oracle_bench_gate(bench: Mapping[str, Any], *, cases: int = BENCH_CASES) -> tuple[bool, str]:
    """oracle 上 bench 的唯一口径：**44 例、FP=0**。少一例都不算跑过。"""
    got = int(bench.get("cases") or 0)
    fp = int(bench.get("fp") or 0)
    if got < cases:
        return False, f"bench 用例 {got} < {cases}"
    if fp > BENCH_FP_LIMIT:
        return False, f"FP={fp} > {BENCH_FP_LIMIT}"
    return True, ""


__all__ = [
    "ADMIT",
    "BENCH_CASES",
    "BENCH_FP_LIMIT",
    "EGRESS_POLICY",
    "ORACLE_INVARIANTS",
    "SOURCE_INVARIANTS",
    "AdmitError",
    "Admission",
    "Candidate",
    "MountPolicy",
    "ReviewLevel",
    "admit",
    "admit_and_install",
    "assert_only_oracle_judges",
    "default_policies",
    "oracle_bench_gate",
]

#: 占位以避免 lint 把模块级常量判为未使用（admit 内联使用了这些值）
ADMIT = "veripen.plugins.admission/v1"
