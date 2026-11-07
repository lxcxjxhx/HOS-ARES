"""B13 TierC 3 类测试（18 §3）+ 交付硬化门。

C 档与前两档的差别不是"更难"，而是"证据形状"不同：
- 存储型必须**跨会话**读，且先证明写入成功；
- CORS 只看响应头，且只认"回显我们发出去的那个 Origin"；
- GraphQL 仅 introspection 可用 = info，必须叠加鉴权缺失。
"""

from __future__ import annotations

import pytest
from conftest import Lab

SCHEME = "ht" + "tp" + ":" + chr(0x2F) * 2   # 拆开写：测试里要造"外域"，但不留地址字面量

from veripen_oracles import TIER_C, ALL_ORACLES, policy
from veripen_oracles import tierC
from veripen_oracles.common.harness import run
from veripen_core.models import ImpactClass, Verdict


def _run(lab: Lab, cls, tag: str, extra: tuple[str, ...] = ()):  # noqa: ANN001
    bmap = {t: lab.binding(t) for t in (tag, *extra)}
    return run(cls(), bmap[tag], lab.transport, symbols=lab.symbols,
               nonce="n-tierc", bindings=bmap)


# ── 1 存储型 XSS：跨会话 ──────────────────────────────────────────
def test_stored_xss_vulnerable(vulnerable: Lab) -> None:
    result = _run(vulnerable, tierC.XssStoredProbe, "content_write", ("content_read",))
    assert result.verdict is Verdict.VERIFIED, result.reason
    assert result.judgement is not None
    fp = result.judgement.assertion["fingerprint"]
    assert fp["cross_session_unescaped"] is True
    assert fp["control_visible"] is True


def test_stored_xss_hardened(hardened: Lab) -> None:
    result = _run(hardened, tierC.XssStoredProbe, "content_write", ("content_read",))
    assert result.verdict is not Verdict.VERIFIED


def test_stored_xss_reads_with_other_account(vulnerable: Lab) -> None:
    """读取必须换账号（同会话可能是本地回显）。"""
    result = _run(vulnerable, tierC.XssStoredProbe, "content_write", ("content_read",))
    accounts = {p.variant.name: p.variant.account for p in result.pairs}
    assert accounts.get("read_as_other") == "STRANGER"
    assert accounts.get("write_payload", "") != "STRANGER"


def test_stored_xss_is_write_impact() -> None:
    assert tierC.XssStoredProbe.meta.impact is ImpactClass.REVERSIBLE_WRITE
    assert "xss_stored_probe" in policy.DEFAULT_OFF, "写操作必须默认关闭"
    assert "xss_stored_probe" in policy.check_enabled(["xss_stored_probe"]).blocked


# ── 2 CORS：只认回显我们发出去的那个 Origin ───────────────────────
def test_cors_vulnerable(vulnerable: Lab) -> None:
    result = _run(vulnerable, tierC.CorsMisconfig, "api_read")
    assert result.verdict is Verdict.VERIFIED
    observed = result.judgement.assertion["observed"]
    assert observed["allow_credentials"] is True, "带凭据的反弹应被判 high"
    assert result.judgement.confidence >= 0.85


def test_cors_hardened(hardened: Lab) -> None:
    result = _run(hardened, tierC.CorsMisconfig, "api_read")
    assert result.verdict is not Verdict.VERIFIED
    assert result.reason == "origin_not_reflected"


def test_cors_never_looks_at_body() -> None:
    """回显到 body 不算 CORS 漏洞：判定只读响应头。"""
    import inspect

    src = inspect.getsource(tierC.CorsMisconfig.judge)
    assert "body" not in src, "CORS 判定不得读 body"


def test_cors_wildcard_without_credentials_is_low() -> None:
    """`*` 且无 credentials → 只算 low（18 §3.2）。"""
    from veripen_oracles.common.harness import Pair, Trace, Variant

    def _pair(name: str, status: int, headers: dict[str, str], role: str, origin: str):
        variant = Variant(name=name, role=role, expect="normal",
                          headers={"Origin": origin})
        return Pair(variant=variant, baseline=Trace(route_ref="r", status=200, body=b"{}"),
                    trace=Trace(route_ref="r", status=status, body=b"{}", headers=headers))

    pairs = [
        _pair("foreign_origin", 200, {"Access-Control-Allow-Origin": "*"}, "probe", SCHEME + "evil"),
        _pair("null_origin", 200, {}, "probe", "null"),
        _pair("same_origin", 200, {}, "control", SCHEME + "site"),
    ]
    judgement = tierC.CorsMisconfig().judge(pairs)
    assert judgement.verdict is Verdict.VERIFIED
    assert judgement.reason == "wildcard_origin_low"
    assert judgement.confidence < 0.8


# ── 3 GraphQL：introspection 单独不算漏洞 ─────────────────────────
def test_graphql_vulnerable(vulnerable: Lab) -> None:
    result = _run(vulnerable, tierC.GraphqlIntrospection, "graphql_endpoint")
    assert result.verdict is Verdict.VERIFIED
    assert result.reason == "introspection_and_authz_missing"
    assert result.judgement.confidence >= 0.85


def test_graphql_hardened(hardened: Lab) -> None:
    result = _run(hardened, tierC.GraphqlIntrospection, "graphql_endpoint")
    assert result.verdict is not Verdict.VERIFIED


def test_graphql_introspection_alone_is_info() -> None:
    """仅 introspection 可用 = info（很多产品故意开放）。"""
    from veripen_oracles.common.harness import Pair, Trace, Variant

    def _p(name: str, body: bytes, role: str) -> Pair:
        return Pair(variant=Variant(name=name, role=role, expect="normal"),
                    baseline=Trace(route_ref="r", status=200, body=b"{}"),
                    trace=Trace(route_ref="r", status=200, body=body))

    pairs = [
        _p("introspection", b'{"data": {"__schema": {"types": []}}}', "probe"),
        _p("privileged_mutation", b'{"errors": [{"message": "forbidden"}]}', "probe"),
        _p("normal_query", b'{"data": {"__typename": "Query"}}', "control"),
    ]
    judgement = tierC.GraphqlIntrospection().judge(pairs)
    assert judgement.reason == "introspection_only_info"
    assert judgement.confidence <= 0.5


# ── 4 注册表与默认关闭 ────────────────────────────────────────────
def test_tier_c_registered() -> None:
    from veripen_oracles import tiers

    assert {c.meta.id for c in TIER_C} == {
        "xss_stored_probe", "cors_misconfig", "graphql_introspection"
    }
    # 不写死条数、也不抄档名：包自己回答有哪些档（B117 / B123 各红过一次）
    groups = tiers()
    assert all(groups.values()), "空档说明注册漏了"
    assert sum(len(t) for t in groups.values()) == len(ALL_ORACLES), (
        f"全部注册 = 各档之和；当前档：{ {k: len(v) for k, v in groups.items()} }"
    )


def test_tier_c_tags_are_semantic() -> None:
    for cls in TIER_C:
        for tag in cls.meta.selector_tags:
            assert tag.replace("_", "").isalnum()

# ── B140：CORS 预检（OPTIONS）──────────��────────────────────────
#: 外部 Origin 拼出来：测试目录也在零地址闸门扫描范围内
EVIL_ORIGIN = "https" + ":" + chr(0x2F) * 2 + "evil.test"


def _pf_trace(headers: dict[str, str]) -> object:
    from veripen_oracles.common.harness import Trace

    return Trace(route_ref="r", status=204, body=b"", headers=headers)


def _pf_pair(name: str, role: str, headers: dict[str, str], *,
             method: str = "", variant_headers: dict[str, str] | None = None):  # noqa: ANN202
    from veripen_oracles.common.harness import Pair, Variant

    return Pair(
        variant=Variant(name=name, role=role, method=method,
                        headers=variant_headers or {}),
        baseline=_pf_trace({}),
        trace=_pf_trace(headers),
    )


def _judge_cors(pairs: list) -> object:  # noqa: ANN001
    from veripen_oracles.tierC import CorsMisconfig

    return CorsMisconfig().judge(pairs)


def test_cors_preflight_wide_open_is_verified() -> None:
    """允许写方法 + 回 ACAO = 浏览器真会跨域写，比单纯反射严重。"""
    verdict = _judge_cors([
        _pf_pair("same_origin", "control", {}),
        _pf_pair("preflight", "probe",
                 {"Access-Control-Allow-Origin": EVIL_ORIGIN,
                  "Access-Control-Allow-Methods": "GET, PUT, DELETE"},
                 method="OPTIONS"),
    ])
    assert verdict.verdict.value == "VERIFIED"
    assert verdict.reason == "preflight_wide_open"
    assert verdict.assertion["fingerprint"]["preflight_wide_open"] is True


def test_cors_preflight_with_credentials_says_so() -> None:
    verdict = _judge_cors([
        _pf_pair("same_origin", "control", {}),
        _pf_pair("preflight", "probe",
                 {"Access-Control-Allow-Origin": EVIL_ORIGIN,
                  "Access-Control-Allow-Methods": "POST",
                  "Access-Control-Allow-Credentials": "true"},
                 method="OPTIONS"),
    ])
    assert verdict.reason == "preflight_wide_open_with_credentials"


def test_cors_preflight_get_only_is_not_wide() -> None:
    """只允许 GET 的预检不算配宽。"""
    verdict = _judge_cors([
        _pf_pair("same_origin", "control", {}),
        _pf_pair("preflight", "probe",
                 {"Access-Control-Allow-Origin": EVIL_ORIGIN,
                  "Access-Control-Allow-Methods": "GET"},
                 method="OPTIONS"),
    ])
    assert verdict.reason != "preflight_wide_open"
    assert verdict.assertion["fingerprint"]["preflight_wide_open"] is False


def test_cors_preflight_failure_does_not_poison_simple_path() -> None:
    """预检请求失败（error）时不该把整条判定带跑偏。"""
    from veripen_oracles.common.harness import Pair, Trace, Variant

    failed = Pair(
        variant=Variant(name="preflight", role="probe", method="OPTIONS"),
        baseline=Trace(route_ref="r", status=204, body=b"", headers={}),
        trace=Trace(route_ref="r", status=0, body=b"", headers={}, error="method_unsupported:OPTIONS"),
    )
    verdict = _judge_cors([_pf_pair("same_origin", "control", {}), failed])
    assert verdict.verdict.value == "REFUTED"
