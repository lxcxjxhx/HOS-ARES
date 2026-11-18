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


# ── B375：`auto` 不等于「能测」 ─────────────────────────────────
def test_auto_checks_are_split_into_implemented_and_paper_only() -> None:
    """矩阵里 15 条 auto，只有 5 条真有判定函数（O1/O5/S1/M1/M7）。

    其余 10 条（M2–M5、O2/O4/O7/O8、S2/S3）只有靶场计划与判据文字，**没有代码**——
    这条断言把它们单列出来，防止"清单里有"被读成"能测"（B9 的 open 拖了十几批）。
    """
    matrix = checks.coverage_matrix(oracle_hits={})
    assert matrix["auto_total"] == 15, matrix["by_automation"]
    # B389/B390/B391/B393/B410：O2、S2、O7 各接一个，M2–M5 共用证书判定函数，
    # S3（登出失效）与 O4（PKCE）补上 → 14
    assert matrix["auto_implemented"] == 14, matrix["auto_implemented"]
    missing = matrix["auto_without_judge"]
    # 名单不写成常量表（lint-nodata 会把长字面量当硬编码数据）：
    # 只钉"数量对得上 + 抽样的确在里面 + 它们真的没有判定函数"
    # 需要多步序列的那几条（O4/O8/S3）不算"只有清单"——它们是执行模型装不下。
    # B393 之后「只有清单」清零：剩下的要么有判定，要么是多步/观测受限。
    assert missing == [], missing
    assert all(not checks.JUDGES.get(cid) for cid in missing), missing


def test_three_tiers_are_disjoint() -> None:
    """有判定 / 只有清单 / 需要多步 —— 三档不许重叠（B392）。

    重叠会让总数读起来是"还剩 7 条没写"，其实其中 3 条是执行模型装不下。
    """
    matrix = checks.coverage_matrix(oracle_hits={})
    implemented = {c["id"] for c in matrix["checks"] if c.get("judge")}
    paper = set(matrix["auto_without_judge"])
    sequence = set(matrix["needs_sequence"])
    unmeasurable = set(matrix["unmeasurable"])
    assert not (paper & sequence), paper & sequence
    assert not (implemented & (paper | sequence)), implemented
    # `unmeasurable` 不是"另一档"，而是一个**属性**：M7 有判定函数，只是观测不到。
    # 所以它必须落在 implemented 里面，不能取代它。
    assert unmeasurable <= implemented, unmeasurable - implemented
    # S3（B410）与 O4（B411）都已实现，只剩 O8
    assert sequence == {"O8"}, sequence


def test_every_declared_judge_exists_and_is_callable() -> None:
    """`JUDGES` 不许指向不存在的函数（改名后静默失效最难查）。"""
    for check_id, fn_name in checks.JUDGES.items():
        assert check_id in {c.id for c in checks.ALL_CHECKS}, check_id
        fn = getattr(checks, fn_name, None)
        assert callable(fn), fn_name


# ── B376：S1 会话固定真的接上了 oracle ──────────────────────────
def test_session_fixation_oracle_is_registered_and_uses_the_judge() -> None:
    """判定函数 → oracle 这条通路：注册表里有它，判据仍走 auth/checks 那一条。"""
    from veripen_oracles import ALL_ORACLES
    from veripen_oracles.auth import session

    ids = {c.meta.id for c in ALL_ORACLES}
    assert "session_fixation" in ids, "没注册就永远跑不到（B9 的 open）"
    assert session.SessionFixation.meta.selector_tags == ("token_issue",)


def test_set_cookie_sid_is_parsed_without_other_attributes() -> None:
    from types import SimpleNamespace

    from veripen_oracles.auth.session import _cookie_sid

    assert _cookie_sid(SimpleNamespace(headers={"Set-Cookie": "sid=abc; Path=/; HttpOnly"})) == "abc"
    assert _cookie_sid(SimpleNamespace(headers={"set-cookie": "a=1; sid=xyz"})) == "xyz"
    assert _cookie_sid(SimpleNamespace(headers={})) == ""
    assert _cookie_sid(SimpleNamespace(headers={"Set-Cookie": "other=1"})) == ""


# ── B377：O5 授权码重放接成 oracle ─────────────────────────────
def test_code_replay_oracle_is_registered_and_disposable_by_default() -> None:
    from veripen_oracles import ALL_ORACLES
    from veripen_oracles.auth import oauth

    ids = {c.meta.id for c in ALL_ORACLES}
    assert "code_replay" in ids
    # 非幂等：默认 repeats=2 会把"一次性"读成"不稳定"，所以必须声明只发一次
    assert oauth.CodeParams().repeat == 1


def test_code_replay_baseline_uses_its_own_code() -> None:
    """基线若消费掉一次性码，后面两发必然 400 → 把自己的探针算成"第一次失败"。"""
    from veripen_oracles.auth.oauth import BASELINE_CODE, FIXED_CODE, CodeReplay

    params = CodeReplay().baseline_params(binding=None)
    assert params["code"] == BASELINE_CODE != FIXED_CODE


# ── B384：O1 接成 oracle ───────────────────────────────────────
def test_oauth_redirect_oracle_is_registered_and_needs_oob() -> None:
    from veripen_oracles import ALL_ORACLES

    ids = {c.meta.id for c in ALL_ORACLES}
    assert "oauth_redirect_uri" in ids
    cls = next(c for c in ALL_ORACLES if c.meta.id == "oauth_redirect_uri")
    # 判据要 oob_hit：没有监听器就说清楚缺什么，不靠 Location 单独下结论
    assert "oob:listener" in cls.meta.requires
    assert cls.meta.selector_tags == ("redirect_entry",)


# ── B386：M7 从客户端侧测不出，要写成事实 ──────────────────────
def test_unmeasurable_checks_are_named_with_a_reason() -> None:
    """M7（服务端压根没请求证书）在客户端侧测不出——不是"没接"，是"测不了"。

    不写进来的话，后人会照着 M1 的模板再试一遍，然后得到一堆
    "握手成功 = 没请求"的错误结论（TLS 1.3 下可选请求与不请求都握手成功）。
    """
    matrix = checks.coverage_matrix(oracle_hits={})
    assert {"M7", "M5"} <= set(matrix["unmeasurable"]), matrix["unmeasurable"]
    assert "CertificateRequest" in matrix["unmeasurable"]["M7"]
    # M5：受信的弱签名证书客户端照收（实测），所以可分类但不可观测
    assert "SHA1" in matrix["unmeasurable"]["M5"].upper(), matrix["unmeasurable"]["M5"]
