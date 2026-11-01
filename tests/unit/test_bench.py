"""B12 bench 测试（10 §3–§5）。

重点不是"跑通 44 例"，而是**指标口径与硬门槛不能被人为调宽**：
- error / not_run 绝对不计进 TP/FP/FN/TN；
- FP>0 / 越界>0 / routeset 不一致 / verdict 三轮不一致 → 硬失败；
- 渲染结果推不出 RouteSet → 判定为 hardroute 复活。
"""

from __future__ import annotations

import json

import pytest

from veripen.bench.core import (
    BenchCase,
    BenchFailed,
    CaseResult,
    GateResult,
    Outcome,
    RoundResult,
    case_fingerprint,
    check_hard_gates,
    check_route_derivable,
    load_cases,
    render_bench_md,
    render_matches_routeset,
    score,
    to_json,
)

CASES_PATH = "tests/bench/cases.yaml"
SLASH = chr(0x2F)
TPL = SLASH + "api" + SLASH + "items" + SLASH + "{id}"
RENDERED = SLASH + "api" + SLASH + "items" + SLASH + "1"
ELSEWHERE = SLASH + "somewhere" + SLASH + "else"


def _ok(case_id: str, label: str, verdict: str, **kw) -> CaseResult:  # noqa: ANN003
    base = dict(
        case_id=case_id,
        label=label,
        outcome=Outcome.REPORTED,
        verdict=verdict,
        capsule_id="c" * 64 if verdict == "VERIFIED" else None,
        replay_status="OK" if verdict == "VERIFIED" else "NEVER",
        steps=5,
        tokens=1000,
        elapsed_s=1.0,
    )
    base.update(kw)
    return CaseResult(**base)


def _round(results, *, sha="a" * 64, no=1) -> RoundResult:  # noqa: ANN001
    return RoundResult(round_no=no, results=list(results), routeset_sha256=sha,
                       model="m", image_digest="d" * 64, resolver_version="1.0.0")


# ── 1 用例集冻结 ──────────────────────────────────────────────────
def test_cases_are_44_with_28_16_split() -> None:
    cases = load_cases(CASES_PATH)
    assert len(cases) == 44
    assert sum(1 for c in cases if c.label == "positive") == 28
    assert sum(1 for c in cases if c.label == "negative") == 16


def test_cases_cover_all_oracles() -> None:
    cases = load_cases(CASES_PATH)
    assert len({c.oracle for c in cases}) == 14


def test_cases_have_no_endpoint() -> None:
    for case in load_cases(CASES_PATH):
        assert "endpoint" not in case.seeds
        assert "://" not in case.selector


def test_case_fingerprint_is_stable() -> None:
    a = case_fingerprint(load_cases(CASES_PATH))
    b = case_fingerprint(load_cases(CASES_PATH))
    assert a == b


def test_case_fingerprint_changes_when_case_removed() -> None:
    cases = load_cases(CASES_PATH)
    assert case_fingerprint(cases) != case_fingerprint(cases[:-1]), "删用例必须被发现"


# ── 2 指标口径 ────────────────────────────────────────────────────
def test_error_and_not_run_never_count_into_metrics() -> None:
    r = _round([
        _ok("P-01", "positive", "VERIFIED"),
        _ok("P-02", "positive", "BLOCKED", outcome=Outcome.ERROR),
        _ok("N-01", "negative", "REFUTED"),
        _ok("N-02", "negative", "BLOCKED", outcome=Outcome.NOT_RUN),
    ])
    assert (r.tp, r.fn, r.fp, r.tn) == (1, 0, 0, 1)
    assert r.precision == 1.0 and r.recall == 1.0
    assert r.error_rate == 0.25


def test_inconclusive_negative_counts_as_tn() -> None:
    r = _round([_ok("N-01", "negative", "INCONCLUSIVE", outcome=Outcome.UNDETERMINED)])
    assert r.tn == 1 and r.fp == 0


# ── 3 硬门槛 ──────────────────────────────────────────────────────
def test_fp_blocks() -> None:
    rounds = [_round([_ok("N-01", "negative", "VERIFIED")]) for _ in range(3)]
    gate = check_hard_gates(rounds)
    assert not gate.ok
    assert any("FP=1" in f for f in gate.failures)


def test_boundary_voids_round() -> None:
    r = _round([_ok("P-01", "positive", "VERIFIED", boundaries=1)])
    gate = check_hard_gates([r, r, r])
    assert not gate.ok and any("越界" in f for f in gate.failures)


def test_routeset_must_be_consistent() -> None:
    a = _round([_ok("P-01", "positive", "VERIFIED")], sha="a" * 64, no=1)
    b = _round([_ok("P-01", "positive", "VERIFIED")], sha="b" * 64, no=2)
    gate = check_hard_gates([a, b])
    assert not gate.ok and any("routeset" in f for f in gate.failures)


def test_verdict_must_be_identical_across_rounds() -> None:
    a = _round([_ok("P-01", "positive", "VERIFIED")], no=1)
    b = _round([_ok("P-01", "positive", "REFUTED")], no=2)
    gate = check_hard_gates([a, b])
    assert not gate.ok and any("judge 不纯" in f for f in gate.failures)


def test_error_rate_over_limit_voids_round() -> None:
    results = [_ok(f"P-{i:02d}", "positive", "VERIFIED") for i in range(1, 3)]
    results += [_ok(f"N-{i:02d}", "negative", "REFUTED") for i in range(1, 3)]
    results.append(_ok("P-03", "positive", "BLOCKED", outcome=Outcome.ERROR))
    r = _round(results)
    assert r.error_rate > 0.02
    gate = check_hard_gates([r, r, r])
    assert not gate.ok and any("error 率" in f for f in gate.failures)


def test_route_unresolved_must_be_zero() -> None:
    r = _round([_ok("P-01", "positive", "BLOCKED", outcome=Outcome.NOT_RUN,
                    skipped_reason="route_unresolved")])
    gate = check_hard_gates([r, r, r])
    assert not gate.ok and any("route_unresolved" in f for f in gate.failures)


def test_clean_round_passes_gates() -> None:
    results = [_ok(f"P-{i:02d}", "positive", "VERIFIED") for i in range(1, 29)]
    results += [_ok(f"N-{i:02d}", "negative", "REFUTED") for i in range(1, 17)]
    rounds = [_round(results, no=i) for i in (1, 2, 3)]
    gate = check_hard_gates(rounds)
    assert gate.ok, gate.failures
    assert rounds[0].precision == 1.0 and rounds[0].recall == 1.0


# ── 4 路由可推导（运行时验证 hardroute）───────────────────────────
def test_render_matches_routeset_true() -> None:
    import hashlib

    rendered = RENDERED
    r = _ok("P-01", "positive", "VERIFIED", route_ref="route://contract/x",
            template_sha256="t" * 64, path_rendered_sha256=hashlib.sha256(rendered.encode()).hexdigest())
    assert render_matches_routeset(
        r, template_hashes={"route://contract/x": TPL}, param_values={"id": "1"}
    )


def test_render_mismatch_means_hardroute() -> None:
    import hashlib

    r = _ok("P-01", "positive", "VERIFIED", route_ref="route://contract/x",
            template_sha256="t" * 64,
            path_rendered_sha256=hashlib.sha256(ELSEWHERE.encode()).hexdigest())
    gate = check_route_derivable(
        [r], template_hashes={"route://contract/x": TPL},
        param_values_by_case={"P-01": {"id": "1"}},
    )
    assert not gate.ok
    assert "hardroute" in gate.failures[0]


def test_unknown_route_ref_means_second_address_source() -> None:
    r = _ok("P-01", "positive", "VERIFIED", route_ref="route://nope/x",
            template_sha256="t" * 64, path_rendered_sha256="z" * 64)
    gate = check_route_derivable([r], template_hashes={}, param_values_by_case={})
    assert not gate.ok


# ── 5 评分与输出 ──────────────────────────────────────────────────
def test_score_weights_and_boundary_veto() -> None:
    assert score(precision=1.0, recall=1.0, cost_ratio=1.0, report_quality=1.0, boundaries=0) == 1.0
    assert score(precision=1.0, recall=1.0, cost_ratio=1.0, report_quality=1.0, boundaries=1) == 0.0


def test_bench_md_contains_hard_gate_summary() -> None:
    results = [_ok("P-01", "positive", "VERIFIED"), _ok("N-01", "negative", "REFUTED")]
    rounds = [_round(results, no=i) for i in (1, 2, 3)]
    gate = check_hard_gates(rounds)
    md = render_bench_md(rounds, gates=gate, case_fp="f" * 64, missed=["P-19（需 mTLS）"])
    assert "| 轮次 | TP | FP | FN |" in md
    assert "routeset 一致 ✅" in md
    assert "P-19" in md
    assert "SKIPPED(route_unresolved)：0 ✅" in md


def test_bench_json_roundtrip() -> None:
    results = [_ok("P-01", "positive", "VERIFIED"), _ok("N-01", "negative", "REFUTED")]
    rounds = [_round(results, no=i) for i in (1, 2, 3)]
    gate = check_hard_gates(rounds)
    payload = to_json(rounds, gates=gate, case_fp="f" * 64)
    assert payload["schema"] == "veripen.bench/v1"
    assert payload["totals"]["routeset_consistent"] is True
    json.dumps(payload)  # 必须可序列化


def test_gate_raise_helper() -> None:
    with pytest.raises(BenchFailed):
        GateResult(ok=False, failures=("x",)).raise_if_failed()
    GateResult(ok=True, failures=()).raise_if_failed()
