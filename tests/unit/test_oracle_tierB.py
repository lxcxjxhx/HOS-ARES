"""B7 TierB 5 类测试 + 启动校验 + 默认关闭。

三件事必须一起测，缺一条这批就是假的：
1. 五类在脆弱态/安全态上判对；
2. 写操作与并发类**默认 enabled:false**，且启动校验真的抛；
3. rate_limit 走 burst 通道，不走三条件骨架（它没有对照组）。
"""

from __future__ import annotations

import pytest
from conftest import Lab

from veripen_oracles import ALL_ORACLES, TIER_B, TIER_A
from veripen_oracles import policy
from veripen_oracles.common.harness import run, sample_burst
from veripen_oracles import tierB
from veripen_core.models import ImpactClass, Verdict

#: (oracle_id, 主绑定标签, 额外绑定)
CASES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("sqli_error_based", "param_injectable", ()),
    ("bola_functional", "admin_function", ("normal_function",)),
    ("xss_reflected_ctx", "echo_param", ()),
    ("mass_assignment", "profile_write", ("profile_read",)),
)


def _by_id(oracle_id: str):  # noqa: ANN202
    for cls in ALL_ORACLES:
        if cls.meta.id == oracle_id:
            return cls()
    raise KeyError(oracle_id)


def _run(lab: Lab, oracle_id: str, tag: str, extras: tuple[str, ...]):  # noqa: ANN202
    bmap = {t: lab.binding(t) for t in (tag, *extras)}
    return run(
        _by_id(oracle_id),
        bmap[tag],
        lab.transport,
        symbols=lab.symbols,
        nonce=f"n-{oracle_id}",
        token=lab.token,
        bindings=bmap,
    )


# ── 1 四类走三条件骨架的：脆弱态 VERIFIED、安全态 REFUTED ───────────
@pytest.mark.parametrize(("oracle_id", "tag", "extras"), CASES)
def test_vulnerable_verified(vulnerable: Lab, oracle_id: str, tag: str, extras: tuple[str, ...]) -> None:
    result = _run(vulnerable, oracle_id, tag, extras)
    assert result.verdict is Verdict.VERIFIED, f"{oracle_id}: {result.reason}"


@pytest.mark.parametrize(("oracle_id", "tag", "extras"), CASES)
def test_hardened_not_verified(hardened: Lab, oracle_id: str, tag: str, extras: tuple[str, ...]) -> None:
    result = _run(hardened, oracle_id, tag, extras)
    assert result.verdict is not Verdict.VERIFIED, f"{oracle_id} 在安全态误报"


# ── 2 rate_limit：走 burst，不走三条件 ─────────────────────────────
def test_rate_limit_vulnerable(vulnerable: Lab) -> None:
    samples = sample_burst(vulnerable.binding("rate_sensitive"), vulnerable.transport, {}, samples=10)
    judgement = tierB.RateLimitAbsent().judge_burst(samples)
    assert judgement.verdict is Verdict.VERIFIED
    assert judgement.confidence == 0.7, "这是'配置缺失'，不是高危（18 §2.5）"


def test_rate_limit_hardened(hardened: Lab) -> None:
    samples = sample_burst(hardened.binding("rate_sensitive"), hardened.transport, {}, samples=10)
    judgement = tierB.RateLimitAbsent().judge_burst(samples)
    assert judgement.verdict is Verdict.REFUTED
    assert judgement.reason == "throttling_observed"


def test_rate_limit_has_no_control_variant(vulnerable: Lab) -> None:
    """这类没有对照组：plan() 必须为空，且 runner 标明走 burst。"""
    oracle = tierB.RateLimitAbsent()
    assert oracle.plan(vulnerable.binding("rate_sensitive")) == []
    assert getattr(oracle, "runner", "") == "burst"


# ── 3 默认关闭 + 启动校验 ──────────────────────────────────────────
def test_default_off_registry_matches_spec() -> None:
    assert policy.DEFAULT_OFF == {"mass_assignment", "rate_limit_absent"}


def test_write_oracle_is_reversible_write() -> None:
    assert tierB.MassAssignment.meta.impact is ImpactClass.REVERSIBLE_WRITE


def test_startup_check_blocks_default_off() -> None:
    decision = policy.check_enabled(["sqli_error_based", "mass_assignment", "rate_limit_absent"])
    assert decision.allowed == ("sqli_error_based",)
    assert decision.blocked == {"mass_assignment": "default_off", "rate_limit_absent": "default_off"}
    with pytest.raises(policy.OracleDisabled):
        decision.raise_if_blocked()


def test_startup_check_allows_after_explicit_enable() -> None:
    decision = policy.check_enabled(
        ["mass_assignment"],
        enabled=["mass_assignment"],
        impact_by_oracle={"mass_assignment": ImpactClass.REVERSIBLE_WRITE},
        ceiling=ImpactClass.REVERSIBLE_WRITE,
    )
    assert decision.blocked == {}


def test_startup_check_blocks_impact_over_ceiling() -> None:
    decision = policy.check_enabled(
        ["mass_assignment"],
        enabled=["mass_assignment"],
        impact_by_oracle={"mass_assignment": ImpactClass.REVERSIBLE_WRITE},
        ceiling=ImpactClass.READ_ONLY,
    )
    assert decision.blocked == {"mass_assignment": "impact_exceeds_ceiling"}


def test_rate_limit_severity_is_capped() -> None:
    assert policy.cap_severity("rate_limit_absent", "high") == "low"
    assert policy.cap_severity("sqli_error_based", "high") == "high"


# ── 4 指纹库与上下文判定（B7 的重点不是"多一个 oracle"）───────────
def test_error_fingerprints_are_explainable() -> None:
    for name, needles in tierB.ERROR_FINGERPRINTS.items():
        assert needles, f"{name} 指纹为空"
    assert tierB.FINGERPRINT_VERSION, "指纹库必须版本化（18 §2.1 误报控制①）"


def test_generic_error_page_is_not_evidence(vulnerable: Lab) -> None:
    """全站通用报错模板：同一指纹在对照也出现 → 必须 REFUTED。"""
    oracle = tierB.SqliErrorBased()
    binding = vulnerable.binding("param_injectable")
    # 让 control 也吐 SQL 错误：把靶场切成"错误页不分参数"
    real = vulnerable.app._search
    vulnerable.app._search = lambda q: (500, "text/html", "SQL syntax error")  # type: ignore[method-assign]
    try:
        result = run(oracle, binding, vulnerable.transport, symbols=vulnerable.symbols, nonce="n")
    finally:
        vulnerable.app._search = real  # type: ignore[method-assign]
    assert result.verdict is not Verdict.VERIFIED


def test_xss_contexts_are_reported_separately(vulnerable: Lab) -> None:
    result = _run(vulnerable, "xss_reflected_ctx", "echo_param", ())
    assert result.judgement is not None
    contexts = result.judgement.assertion["observed"]["unescaped_contexts"]
    assert "html_body" in contexts and "attr_context" in contexts and "js_string" in contexts
    assert result.judgement.assertion["observed"]["offsets"], "证据要带命中偏移（18 §2.3）"


def test_bola_reports_account_unusable(tierB_lab=None) -> None:  # noqa: ANN001
    """低权账号本身不能用 → INCONCLUSIVE，不判成漏洞也不判成安全。"""
    lab = Lab(secure=False)
    lab.transport.send = lambda b, p, r, h=None: _unauth_trace(b, p, r)  # type: ignore[assignment]
    result = run(
        tierB.BolaFunctional(),
        lab.binding("admin_function"),
        lab.transport,
        symbols=lab.symbols,
        bindings={"admin_function": lab.binding("admin_function"),
                  "normal_function": lab.binding("normal_function")},
    )
    assert result.verdict is Verdict.INCONCLUSIVE


def _unauth_trace(binding, params, role):  # noqa: ANN001, ANN202
    from veripen_oracles.common.harness import Trace

    if role == "baseline":
        return Trace(route_ref=binding.ref, status=200, body=b'{"admin": true}')
    return Trace(route_ref=binding.ref, status=401, body=b'{"error": "unauthenticated"}')


def test_tiers_are_disjoint_and_complete() -> None:
    ids_a = {c.meta.id for c in TIER_A}
    ids_b = {c.meta.id for c in TIER_B}
    assert not ids_a & ids_b
    assert len(ids_a) == 6 and len(ids_b) == 5, "A 档 6 + B 档 5 = 11 类（18 §0）"
