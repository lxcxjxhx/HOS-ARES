"""B12.12 装配与准入测试（36 §4）。

两条 DoD：
- **oracle 必过 44 例**（少一例都不算跑过，FP>0 直接拒）；
- **插件直连被拦**（红线 P1：插件进程永远不能直接访问目标）。

外加一条这层真正的价值：**装 100 个 knowledge 是安全的，装 1 个 oracle 是重流程**
——不是靠文案，是靠 `admit()` 返回拒绝。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from veripen_core.actions import BINARY_WHITELIST
from veripen_core.plugins.admission import (
    BENCH_CASES,
    ORACLE_INVARIANTS,
    SOURCE_INVARIANTS,
    Admission,
    Candidate,
    MountPolicy,
    ReviewLevel,
    admit,
    admit_and_install,
    assert_only_oracle_judges,
    default_policies,
    oracle_bench_gate,
)
from veripen_core.plugins.registry import (
    Mount,
    PluginRecord,
    Registry,
    Source,
    Trust,
)

NOW = datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc)
SHA = "a" * 64


def _rec(mount: Mount, *, trust: Trust = Trust.REVIEWED, version: str = "1.0.0") -> PluginRecord:
    return PluginRecord(
        id=f"gh:o/{mount.value}", kind=mount.value, scenario=mount,
        installed_version=version,
        source=Source(type="github", repo=f"o/{mount.value}", commit="abc"),
        install_method="raw", installed_at=NOW.isoformat(), trust=trust,
    )


def _good(mount: Mount, **kw) -> Candidate:  # noqa: ANN003
    base = dict(
        record=_rec(mount), content_sha256=SHA, signature="sig:abc",
        invariants_ok=frozenset(), bench={"cases": BENCH_CASES, "fp": 0},
        creates_process=(mount is Mount.ACT), direct_egress=False,
        requested_binaries=("nmap",) if mount is Mount.ACT else (),
        route_test_cases=3 if mount is Mount.SOURCE else 0,
    )
    base.update(kw)
    return Candidate(**base)  # type: ignore[arg-type]


# ══════════════════════════════════════════════════════════════════
# 1 红线 P1：插件直连被拦
# ══════════════════════════════════════════════════════════════════
def test_direct_egress_is_rejected_on_every_mount() -> None:
    """DoD：插件直连被拦。四类挂载点一个都不能例外。"""
    for mount in Mount:
        r = admit(_good(mount, direct_egress=True,
                        invariants_ok=ORACLE_INVARIANTS if mount is Mount.ORACLE else
                        SOURCE_INVARIANTS if mount is Mount.SOURCE else frozenset()))
        assert r.admitted is False
        assert any("红线 P1" in x for x in r.reasons), (mount, r.reasons)


def test_proxied_plugin_passes() -> None:
    assert admit(_good(Mount.KNOWLEDGE)).admitted is True


# ══════════════════════════════════════════════════════════════════
# 2 knowledge：装 100 个是安全的
# ══════════════════════════════════════════════════════════════════
def test_knowledge_admits_freely_and_never_judges() -> None:
    r = admit(_good(Mount.KNOWLEDGE))
    assert r.admitted is True
    assert r.may_judge is False
    assert r.reasons == ()


def test_one_hundred_knowledge_plugins_all_admit() -> None:
    """"装 100 个 knowledge 是安全的"——这句话要能被跑出来，不是文案。"""
    results = [admit(_good(Mount.KNOWLEDGE)) for _ in range(100)]
    assert all(r.admitted for r in results)
    assert not any(r.may_judge for r in results)


# ══════════════════════════════════════════════════════════════════
# 3 act：必须沙箱 + 二进制白名单
# ══════════════════════════════════════════════════════════════════
def test_act_without_sandbox_is_rejected() -> None:
    r = admit(_good(Mount.ACT, creates_process=False))
    assert r.admitted is False
    assert any("要求沙箱" in x for x in r.reasons)


def test_act_rejects_binary_outside_whitelist() -> None:
    r = admit(_good(Mount.ACT, requested_binaries=("sqlmap", "nmap")))
    assert r.admitted is False
    assert any("白名单外二进制" in x for x in r.reasons)


def test_act_whitelist_comes_from_actions_layer() -> None:
    """白名单只有一个来源：白名单在 actions/ 里定义，准入只读它。"""
    pol = default_policies()[Mount.ACT]
    assert pol.binary_whitelist == BINARY_WHITELIST


def test_act_can_never_judge() -> None:
    assert admit(_good(Mount.ACT)).may_judge is False


# ══════════════════════════════════════════════════════════════════
# 4 source：不变量 + 用例数
# ══════════════════════════════════════════════════════════════════
def test_source_requires_four_invariants() -> None:
    r = admit(_good(Mount.SOURCE, invariants_ok=frozenset({"N1", "N2"})))
    assert r.admitted is False
    assert any("未满足不变量" in x for x in r.reasons)


def test_source_with_all_invariants_passes() -> None:
    assert admit(_good(Mount.SOURCE, invariants_ok=SOURCE_INVARIANTS)).admitted is True


def test_source_requires_three_route_test_cases() -> None:
    r = admit(_good(Mount.SOURCE, invariants_ok=SOURCE_INVARIANTS, route_test_cases=2))
    assert r.admitted is False
    assert any("< 3" in x for x in r.reasons)


def test_source_requires_human_review() -> None:
    r = admit(_good(Mount.SOURCE, invariants_ok=SOURCE_INVARIANTS, record=_rec(Mount.SOURCE, trust=Trust.AUTO)))
    assert r.admitted is False
    assert any("人工 review" in x for x in r.reasons)


# ══════════════════════════════════════════════════════════════════
# 5 oracle：签名 + review + 44 例 FP=0
# ══════════════════════════════════════════════════════════════════
def _oracle(**kw) -> Candidate:  # noqa: ANN003
    kw.setdefault("invariants_ok", ORACLE_INVARIANTS)
    return _good(Mount.ORACLE, **kw)


def test_oracle_admits_with_full_proof() -> None:
    r = admit(_oracle())
    assert r.admitted is True
    assert r.may_judge is True  # 唯一能判的挂载点


def test_oracle_without_signature_is_rejected() -> None:
    r = admit(_oracle(signature=""))
    assert r.admitted is False
    assert any("签名" in x for x in r.reasons)


def test_oracle_bench_must_be_44_cases() -> None:
    """DoD：oracle 必过 44 例。少一例都不算跑过。"""
    r = admit(_oracle(bench={"cases": 43, "fp": 0}))
    assert r.admitted is False
    assert any("43 例 < 44" in x for x in r.reasons)


def test_oracle_bench_with_false_positive_is_rejected() -> None:
    r = admit(_oracle(bench={"cases": 44, "fp": 1}))
    assert r.admitted is False
    assert any("FP=1" in x for x in r.reasons)


def test_oracle_bench_gate_is_the_single_authority() -> None:
    assert oracle_bench_gate({"cases": 44, "fp": 0}) == (True, "")
    ok, why = oracle_bench_gate({"cases": 10, "fp": 0})
    assert ok is False and "10 < 44" in why


def test_oracle_requires_human_bench_review() -> None:
    r = admit(_oracle(record=_rec(Mount.ORACLE, trust=Trust.AUTO)))
    assert r.admitted is False
    assert any("人工 review" in x for x in r.reasons)


def test_all_oracle_failures_are_reported_at_once() -> None:
    """一次列全所有理由：让装的人一次就改对，不是挤牙膏。"""
    r = admit(_oracle(signature="", bench={"cases": 20, "fp": 3},
                      invariants_ok=frozenset(), record=_rec(Mount.ORACLE, trust=Trust.AUTO)))
    assert r.admitted is False
    assert len(r.reasons) >= 4


# ══════════════════════════════════════════════════════════════════
# 6 装配后自检
# ══════════════════════════════════════════════════════════════════
def test_only_oracle_judges_after_assembly() -> None:
    admissions = [
        admit(_good(Mount.KNOWLEDGE)),
        admit(_good(Mount.ACT)),
        admit(_good(Mount.SOURCE, invariants_ok=SOURCE_INVARIANTS)),
        _oracle_admission(),
    ]
    assert_only_oracle_judges(admissions)  # 不抛
    assert sum(a.may_judge for a in admissions) == 1


def _oracle_admission() -> Admission:
    return admit(_oracle())


def test_policy_table_matches_doc_36() -> None:
    pols = default_policies()
    assert pols[Mount.KNOWLEDGE].allow_install == "true"
    assert pols[Mount.KNOWLEDGE].review is ReviewLevel.NONE
    assert pols[Mount.ACT].sandbox == "required"
    assert pols[Mount.SOURCE].allow_install == "manual"
    assert pols[Mount.ORACLE].signature_required is True
    assert pols[Mount.ORACLE].bench_required is True
    assert pols[Mount.ORACLE].review is ReviewLevel.HUMAN_BENCH


# ══════════════════════════════════════════════════════════════════
# 7 准入通过才装机，且留下 lock 快照
# ══════════════════════════════════════════════════════════════════
def test_admit_and_install_writes_registry_and_lock() -> None:
    reg = Registry()
    adm, snap = admit_and_install(_oracle(), reg, now=NOW)
    assert adm.admitted is True
    assert reg.get("gh:o/oracle") is not None
    assert reg.lock_of("gh:o/oracle") is not None
    assert snap["snapshot_sha256"] == adm.lock_snapshot["snapshot_sha256"]


def test_rejected_candidate_is_not_installed() -> None:
    """拒绝就是拒绝：清单里不该留下半条记录。"""
    reg = Registry()
    adm, snap = admit_and_install(_oracle(bench={"cases": 1, "fp": 0}), reg, now=NOW)
    assert adm.admitted is False
    assert reg.get("gh:o/oracle") is None
    assert snap == {}


def test_lock_snapshot_is_part_of_the_admission_evidence() -> None:
    reg = Registry()
    admit_and_install(_good(Mount.KNOWLEDGE), reg, now=NOW)
    adm, snap = admit_and_install(
        _good(Mount.KNOWLEDGE, record=PluginRecord(
            **{**_rec(Mount.KNOWLEDGE).__dict__, "id": "gh:o/k2"})),
        reg, now=NOW,
    )
    # 两次装配之间的 lock 快照不同——快照记录的是"那一刻装了什么版本"
    assert snap["lock"]["gh:o/k2"] is not None
    assert adm.lock_snapshot == snap


def test_policy_override_can_forbid_install() -> None:
    pols = dict(default_policies())
    pols[Mount.KNOWLEDGE] = MountPolicy(
        Mount.KNOWLEDGE, allow_install="false", sandbox="none", review=ReviewLevel.NONE)
    r = admit(_good(Mount.KNOWLEDGE), policies=pols)
    assert r.admitted is False
    assert any("禁止安装" in x for x in r.reasons)
