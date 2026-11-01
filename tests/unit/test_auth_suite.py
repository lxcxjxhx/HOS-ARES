"""B9 认证清单测试：J1–J10 + O/M/S 检查单 + 覆盖矩阵。

要证明的不是"能跑"，而是三件容易糊弄过去的事：
1. 200 不算证据——没有身份标记字段的 200 必须 INCONCLUSIVE；
2. 没有 control（无效签名被拒）时不许出洞；
3. token 只存摘要，签名原文不入证据。
"""

from __future__ import annotations

import base64
import json

import pytest
from conftest import Lab

from veripen_oracles.auth import checks
from veripen_oracles.auth.jwt_suite import (
    JCase,
    JwtSuite,
    b64d,
    b64e,
    forge,
    split_token,
    token_summary,
)
from veripen_oracles.common.harness import Pair, Trace, pair_up, run
from veripen_core.models import Verdict


def _run(lab: Lab):  # noqa: ANN202
    return run(
        JwtSuite(),
        lab.binding("protected_read"),
        lab.transport,
        symbols=lab.symbols,
        nonce="n-jwt",
        token=lab.token,
    )


# ── 1 J 系列的判定 ──────────────────────────────────────────────────
def test_vulnerable_is_verified(vulnerable: Lab) -> None:
    result = _run(vulnerable)
    assert result.verdict is Verdict.VERIFIED, result.reason
    assert result.judgement is not None


def test_hardened_is_refuted(hardened: Lab) -> None:
    result = _run(hardened)
    assert result.verdict is not Verdict.VERIFIED


def test_j_cases_cover_j1_to_j10() -> None:
    ids = [c.id for c in JwtSuite().cases()]
    assert ids == [f"J{i}" for i in range(1, 11)], "20 §2 的十条必须全部在表里"


def test_accepted_cases_are_reported_individually(vulnerable: Lab) -> None:
    result = _run(vulnerable)
    assert result.judgement is not None
    accepted = result.judgement.assertion["observed"]["accepted"]
    # 报告要按 J 编号分开写（20 §4：混在一起是外行报告）
    assert accepted and all(item.startswith("case_j") for item in accepted)
    assert "case_j2" in accepted or "case_j3" in accepted


# ── 2 取证纪律：200 不是证据 ───────────────────────────────────────
def test_200_without_marker_is_inconclusive() -> None:
    """变体 200 但响应里没有身份标记字段 → INCONCLUSIVE，不是 VERIFIED。"""
    oracle = JwtSuite()
    baseline = Trace(route_ref="r", status=200, body=b'{"sub": "secondary"}')
    empty_ok = Trace(route_ref="r", status=200, body=b'{"status": "ok"}')
    control = Trace(route_ref="r", status=401, body=b"{}")
    pairs = pair_up(
        baseline,
        [
            (_as_variant("case_j3"), empty_ok, (empty_ok,)),
            (_as_variant("invalid_signature", role="control"), control, (control,)),
        ],
    )
    judgement = oracle.judge(pairs)
    assert judgement.verdict is Verdict.INCONCLUSIVE
    assert judgement.reason == "no_discriminating_field"


def test_control_not_denied_blocks_verdict(vulnerable: Lab) -> None:
    """服务器对无效签名也放行 → 无法证明它在验签，不许出洞。"""
    oracle = JwtSuite()
    baseline = Trace(route_ref="r", status=200, body=b'{"sub": "sec"}')
    accepted = Trace(route_ref="r", status=200, body=b'{"sub": "primary"}')
    control_ok = Trace(route_ref="r", status=200, body=b'{"sub": "sec"}')
    pairs = pair_up(
        baseline,
        [
            (_as_variant("case_j3"), accepted, (accepted,)),
            (_as_variant("invalid_signature", role="control"), control_ok, (control_ok,)),
        ],
    )
    judgement = oracle.judge(pairs)
    assert judgement.verdict is Verdict.REFUTED
    assert judgement.reason == "control_not_denied"


def _as_variant(name: str, role: str = "probe"):  # noqa: ANN202
    from veripen_oracles.common.harness import Variant

    return Variant(name=name, role=role, expect="accepted_or_rejected")


# ── 3 token 只存摘要 ────────────────────────────────────────────────
def test_token_summary_drops_signature_bytes() -> None:
    token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzIn0.SUPERSECRETSIGNATUREVALUE12"
    summary = token_summary(token)
    assert summary["alg"] == "HS256"
    assert "signature" in summary
    dumped = json.dumps(summary)
    assert "SUPERSECRET" not in dumped, "签名原文绝不入证据"
    assert summary["signature"]["len"] == len("SUPERSECRETSIGNATUREVALUE12")


def test_split_and_forge_roundtrip() -> None:
    token = forge(b64e({"alg": "HS256"}) + "." + b64e({"sub": "a"}) + ".sig0123456789")
    assert len(split_token(token)) == 3
    none_tok = forge(token, header={"alg": "none"}, drop_signature=True)
    assert none_tok.endswith(".")
    assert b64d(split_token(none_tok)[0])["alg"] == "none"


# ── 4 OAuth / mTLS / Session 判定 ──────────────────────────────────
def test_oauth_redirect_judgement_ignores_body_echo() -> None:
    ok = checks.judge_redirect_uri(oob_hit=True, location_ok=True, control_location_ok=True)
    assert ok.verdict == "VERIFIED"
    refuted = checks.judge_redirect_uri(oob_hit=False, location_ok=True, control_location_ok=True)
    assert refuted.verdict == "REFUTED"
    # 对照不跳 = 结论未经检验
    blind = checks.judge_redirect_uri(oob_hit=True, location_ok=True, control_location_ok=False)
    assert blind.verdict == "INCONCLUSIVE"


def test_oauth_code_replay() -> None:
    assert checks.judge_code_replay(first_ok=True, second_ok=True).verdict == "VERIFIED"
    assert checks.judge_code_replay(first_ok=True, second_ok=False).verdict == "REFUTED"
    assert checks.judge_code_replay(first_ok=False, second_ok=False).verdict == "INCONCLUSIVE"


def test_session_fixation() -> None:
    assert checks.judge_session_fixation(before_id="a", after_id="a").verdict == "VERIFIED"
    assert checks.judge_session_fixation(before_id="a", after_id="b").verdict == "REFUTED"
    assert checks.judge_session_fixation(before_id="", after_id="").verdict == "INCONCLUSIVE"


def test_mtls_handshake_reasons_are_distinguished() -> None:
    """M1 与 M3 不能混：自签证书导致的 unknown ca 说明 M1 其实没被测到。"""
    required = checks.judge_mtls_handshake(
        handshake_ok=False, error_reason="certificate required", client_cert_requested=True
    )
    assert required.verdict == "REFUTED"

    unknown_ca = checks.judge_mtls_handshake(
        handshake_ok=False, error_reason="unknown ca", client_cert_requested=True
    )
    assert unknown_ca.verdict == "INCONCLUSIVE"
    assert unknown_ca.reason == "measured_M3_not_M1"


def test_mtls_one_way() -> None:
    assert checks.judge_mtls_one_way(client_cert_requested=False).verdict == "VERIFIED"
    assert checks.judge_mtls_one_way(client_cert_requested=True).verdict == "REFUTED"


# ── 5 覆盖矩阵：0 命中必须列出来 ───────────────────────────────────
def test_coverage_matrix_lists_zero_hits() -> None:
    matrix = checks.coverage_matrix(oracle_hits={"sqli_bool_diff": 3, "graphql_introspection": 0})
    assert matrix["zero_hit_oracles"] == ["graphql_introspection"]
    assert matrix["manual_count"] >= 4, "SAML 的四条必须算作手工项，不许藏"


def test_manual_items_must_explain_why() -> None:
    for item in checks.ALL_CHECKS:
        if item.automation == "manual":
            assert item.why_manual, f"{item.id} 是手工项但没写原因（20 §4：不许写'太麻烦'）"


def test_all_check_ids_unique_and_ordered() -> None:
    ids = [i.id for i in checks.ALL_CHECKS]
    assert len(ids) == len(set(ids))
    for prefix, group in (
        ("O", checks.OAUTH_CHECKS),
        ("M", checks.MTLS_CHECKS),
        ("S", checks.SESSION_CHECKS),
        ("X", checks.SAML_CHECKS),
    ):
        assert all(i.id.startswith(prefix) for i in group)
