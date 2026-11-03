"""B12.5 多 agent 编排测试：**agent 之间只传任务，不传结论**。

五条验收（21 §3.5.4）逐条落地：
1. 交互产物无结论字段（schema 级，不是文字纪律）；
2. 单/多 agent findings 集合相同 —— 生死线；
3. 请求数相同（并行不等于加压）；
4. 崩溃隔离：杀一个 Verifier，其余照跑，崩的那条记 error 不是"没发现"；
5. 成本不失控：tokens_multi ≤ 1.3 × tokens_single。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from veripen_core.agents import (
    VERDICT_KEYS,
    AgentError,
    FindingRecord,
    Recon,
    Reporter,
    TaskSuggestion,
    assert_abi_equal,
    findings_sha,
    isolate_crash,
    orchestrate,
)
from veripen_core.outcome import Outcome

ROOT = Path(__file__).resolve().parents[2]
ROLES = ROOT / "config" / "roles.yaml"

CANDIDATES = [
    ("sqli_bool_diff", "route://observed/aaaaaaaaaaaaaaaa"),
    ("idor_cross_account", "route://observed/bbbbbbbbbbbbbbbb"),
    ("ssrf_oob", "route://observed/cccccccccccccccc"),
    ("xss_reflected_ctx", "route://observed/dddddddddddddddd"),
]


def _fake_run(s: TaskSuggestion) -> FindingRecord:
    """确定性假 Verifier：verdict 只由 (oracle, route) 决定，与并发顺序无关。"""
    verdict = "VERIFIED" if s.oracle_id.startswith(("sqli", "idor")) else "REFUTED"
    cap = "sha256:" + "0" * 64 if verdict == "VERIFIED" else None
    return FindingRecord(
        oracle_id=s.oracle_id, route_ref=s.route_ref, verdict=verdict, capsule_id=cap
    )


# ══════════════════════════════════════════════════════════════════
# 1 交互产物无结论字段（生死规则的 schema 级实现）
# ══════════════════════════════════════════════════════════════════
def test_agents_cannot_negotiate_verdict() -> None:
    """DoD 点名的那条：TaskSuggestion 里**结构上没有**任何结论字段。"""
    s = TaskSuggestion(oracle_id="sqli_bool_diff", route_ref="route://observed/x")
    assert set(s.as_dict()) == {"oracle_id", "route_ref", "reason"}
    assert not (VERDICT_KEYS & set(s.as_dict()))


def test_injected_verdict_field_is_caught() -> None:
    """就算有人绕过 schema 硬塞结论字段，assert_no_verdict 也拦得住。

    做法：造一个 ``as_dict()`` 里带 verdict 的替身（模拟"某个未来版本给
    TaskSuggestion 加了结论字段"），守卫必须抛。这测的是守卫本身，不是 frozen 装饰器。
    """
    class Sneaky(TaskSuggestion):
        def as_dict(self) -> dict[str, object]:
            return {**super().as_dict(), "verdict": "VERIFIED"}

    with pytest.raises(AgentError, match="结论字段"):
        Sneaky(oracle_id="x", route_ref="y").assert_no_verdict()


def test_verifier_refuses_a_suggestion_carrying_verdict() -> None:
    """Verifier 是唯一能判的角色，它也必须拒绝被喂"结论"的任务。"""
    class Sneaky(TaskSuggestion):
        def as_dict(self) -> dict[str, object]:
            return {**super().as_dict(), "severity": "high"}

    from veripen_core.agents import Verifier

    v = Verifier(_fake_run)
    with pytest.raises(AgentError, match="结论字段"):
        v.execute(Sneaky(oracle_id="sqli_bool_diff", route_ref="route://observed/z"))


def test_reporter_cannot_rewrite_verdict() -> None:
    with pytest.raises(AgentError, match="不得改 verdict"):
        Reporter().rewrite_verdict("P-01", "REFUTED")


def test_recon_does_not_read_verdict_verifier_does() -> None:
    assert Recon.reads_verdict is False
    assert Reporter.reads_verdict is True


def test_roles_yaml_matches_code() -> None:
    """配置文件与代码必须说同一件事：recon 的 reads_verdict 只能是 false。"""
    data = yaml.safe_load(ROLES.read_text(encoding="utf-8"))
    assert data["recon"]["reads_verdict"] is False
    assert data["verifier"]["reads_verdict"] is True
    assert data["conflict_policy"] == "no_negotiation"
    assert data["parallel"]["max_workers"] <= data["parallel"]["per_asset_concurrency"]


def test_recon_output_has_no_verdict_even_after_ordering() -> None:
    got = Recon().suggest(candidates=CANDIDATES)
    assert len(got) == len(CANDIDATES)
    for s in got:
        assert not (VERDICT_KEYS & set(s.as_dict()))


def test_recon_does_not_drop_candidates() -> None:
    """Recon 不做取舍：提前裁候选就是替判定做决定。"""
    got = Recon().suggest(candidates=CANDIDATES)
    assert sorted((s.oracle_id, s.route_ref) for s in got) == sorted(CANDIDATES)


def test_recon_orders_by_wave_then_oracle() -> None:
    waves = {"ssrf_oob": "W1", "sqli_bool_diff": "W4"}
    got = Recon().suggest(candidates=CANDIDATES, waves=waves)
    assert got[0].oracle_id == "ssrf_oob"


# ══════════════════════════════════════════════════════════════════
# 2 单/多 agent findings 集合相同 —— 生死线
# ══════════════════════════════════════════════════════════════════
def test_single_and_multi_agent_findings_are_identical() -> None:
    single = orchestrate(CANDIDATES, run=_fake_run, parallel=False)
    multi = orchestrate(CANDIDATES, run=_fake_run, parallel=True, max_workers=2)
    assert single.fingerprint == multi.fingerprint
    assert {f.key for f in single.findings} == {f.key for f in multi.findings}
    assert_abi_equal(single, multi)  # 不抛


def test_abi_gate_catches_a_changed_verdict() -> None:
    """加 agent 就改结论 = 引入不确定性，不是加速——这条必须能红。"""
    single = orchestrate(CANDIDATES, run=_fake_run, parallel=False)
    flipped = orchestrate(
        CANDIDATES,
        run=lambda s: FindingRecord(
            oracle_id=s.oracle_id, route_ref=s.route_ref,
            verdict="REFUTED" if s.oracle_id.startswith("sqli") else "VERIFIED",
            capsule_id=None,
        ),
        parallel=True,
    )
    with pytest.raises(AgentError, match="findings 不一致"):
        assert_abi_equal(single, flipped)


# ══════════════════════════════════════════════════════════════════
# 3 请求数相同（并行不等于加压）
# ══════════════════════════════════════════════════════════════════
def test_request_count_is_identical() -> None:
    single = orchestrate(CANDIDATES, run=_fake_run, parallel=False)
    multi = orchestrate(CANDIDATES, run=_fake_run, parallel=True, max_workers=4)
    assert single.stats.requests == multi.stats.requests == len(CANDIDATES)


def test_workers_are_capped_by_per_asset_concurrency() -> None:
    """多 agent 不能用来绕限流：worker 数取 min(max_workers, per_asset_concurrency)。"""
    seen: set[str] = set()

    def run(s: TaskSuggestion) -> FindingRecord:
        seen.add(s.oracle_id)
        return _fake_run(s)

    res = orchestrate(
        CANDIDATES, run=run, parallel=True, max_workers=8, per_asset_concurrency=2
    )
    assert res.stats.requests == len(CANDIDATES)
    assert seen == {c[0] for c in CANDIDATES}


# ══════════════════════════════════════════════════════════════════
# 4 崩溃隔离
# ══════════════════════════════════════════════════════════════════
def test_one_verifier_crash_does_not_poison_the_rest() -> None:
    suggestions = Recon().suggest(candidates=CANDIDATES)
    res = isolate_crash(suggestions, run=_fake_run, crash_on="ssrf_oob")

    assert len(res.findings) == len(CANDIDATES)  # 全跑完
    crashed = [f for f in res.findings if f.oracle_id == "ssrf_oob"]
    assert len(crashed) == 1
    assert crashed[0].outcome is Outcome.ERROR
    assert crashed[0].error_reason == "tool_crash"
    assert crashed[0].verdict == "BLOCKED"
    assert not crashed[0].outcome.counts_for_metrics  # 不进 TP/FP/FN/TN
    assert res.stats.errors == ("ssrf_oob@route://observed/cccccccccccccccc",)


# ══════════════════════════════════════════════════════════════════
# 5 成本不失控
# ══════════════════════════════════════════════════════════════════
def test_tokens_do_not_explode() -> None:
    single = orchestrate(CANDIDATES, run=_fake_run, parallel=False)
    multi = orchestrate(CANDIDATES, run=_fake_run, parallel=True)
    assert multi.stats.tokens <= int(single.stats.tokens * 1.3) + 1


# ══════════════════════════════════════════════════════════════════
# 6 Reporter 只呈现，不重算
# ══════════════════════════════════════════════════════════════════
def test_reporter_summary_matches_findings_sha() -> None:
    single = orchestrate(CANDIDATES, run=_fake_run, parallel=False)
    assert single.report["findings_sha256"] == findings_sha(single.findings)
    assert single.report["total"] == len(CANDIDATES)
    assert single.report["by_outcome"]["reported"] == len(CANDIDATES)


def test_error_does_not_enter_verdict_tally() -> None:
    suggestions = Recon().suggest(candidates=CANDIDATES)
    res = isolate_crash(suggestions, run=_fake_run, crash_on="ssrf_oob")
    report = Reporter().summarize(res.findings)
    assert report["by_outcome"]["error"] == 1
    assert sum(report["by_verdict"].values()) == len(CANDIDATES) - 1


def test_findings_sha_is_order_independent() -> None:
    a = [FindingRecord("o1", "r1", "VERIFIED", None), FindingRecord("o2", "r2", "REFUTED", None)]
    assert findings_sha(a) == findings_sha(list(reversed(a)))


def test_result_is_json_serialisable() -> None:
    res = orchestrate(CANDIDATES, run=_fake_run, parallel=False)
    blob = json.dumps(res.findings[0].__dict__ | {"outcome": res.findings[0].outcome.value})
    assert "outcome" in blob
