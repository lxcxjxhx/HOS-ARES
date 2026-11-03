"""证据链的端到端验收：**真的**造 capsule、**真的**重放。

在这批之前，``run_bench`` 的 ``capsule_id`` 是 case_id 拼上字面后缀再 sha256、
``replay_status`` 是 ``"OK" if VERIFIED else "NEVER"``——两列都是常量。
也就是说 bench 报告里的"复现通过"从来没有对应过一份真实证据。

四条测试各自守一件事：

1. 造得出、seal 得住（``verify_self_sha`` 通过）；
2. 多面 oracle 的每一步记**各自的**面（这是实际踩到的 bug，见下）；
3. 能重放成功（``OK``）——这是"每条发现能复现"的直接证据；
4. **换一个不满足条件的目标就复现不了**——证明第 3 条不是永远绿。

第 2 条来自一次真实失败：早期版本给所有步骤都记主绑定的 ref，
于是 bola（管理面 + 普通面）、mass_assignment（写 + 回读）、xss_stored（写 + 读）
的第二面在证据里丢了，重放把读请求打到写面上，8 条 VERIFIED 全落 PARTIAL。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests", ROOT / "scripts" / "b12", ROOT / "tests" / "unit"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

SEP = chr(0x2F)


def _run_case(case_id: str):  # noqa: ANN202
    """在真靶场上跑一例，返回 (case, result, capsule, bindings)。"""
    import run_bench as RB

    from lab.app import make_token
    from lab.net_server import LabServer
    from lab.net_transport import NetTransport
    from veripen.bench import load_cases
    from veripen.bench.capsule import build_capsule
    from veripen.bench.faces import extra_tags
    from veripen_core.evidence import seal_capsule_doc
    from veripen_oracles.oob import LabOob

    case = next(c for c in load_cases(ROOT / "tests" / "bench" / "cases.yaml")
                if c.case_id == case_id)
    # 按用例的 label/seed 选靶场态（与 run_bench._pick_lab 同口径）：
    # 负样本要跑在**安全态**上，硬编码 secure=False 会让负样本测的是脆弱态，
    # 结论自然不是 REFUTED。
    app = RB._pick_lab(case)
    srv = LabServer(app).start()
    try:
        rs = RB._routeset(srv.base_url, (case.selector,))
        binding = rs.bindings[0]
        bindings = {case.selector: binding}
        for tag in extra_tags(case.oracle):
            bindings[tag] = RB._routeset(srv.base_url, (tag,)).bindings[0]
        transport = NetTransport(
            token_for=make_token, oob=LabOob(app=app, endpoint_value="oob-SAMPLE")
        )
        result = RB.run_oracle(
            RB._oracle(case.oracle), binding, transport, symbols=RB.SYMBOLS,
            nonce=f"bench-{case_id}", token="evidence-test", bindings=bindings,
        )
        cap = seal_capsule_doc(
            build_capsule(
                case_id=case.case_id, oracle_id=case.oracle,
                oracle_version="test", verdict=result.verdict,
                judgement=result.judgement, pairs=result.pairs,
                baseline_params=result.baseline_params, routeset=rs,
                route_ref=binding.ref, binding=binding, bindings_by_tag=bindings,
                created_at=RB.ROUND_CLOCK[0],
            )
        )
        return case, result, cap
    finally:
        srv.stop()


def _replay(cap, pairs, baseline_params, *, secure: bool) -> str:  # noqa: ANN001
    """在**新实例**上重放，返回 ReplayStatus 字符串。

    新实例不是洁癖：写类 oracle 在同一实例上重放会改掉被测状态，
    测出来就是"重放把靶场弄脏了"而不是"结论复不复现"。
    """
    import run_bench as RB

    from lab.app import App, make_token
    from lab.net_server import LabServer
    from lab.net_transport import NetTransport
    from veripen.bench.capsule import StepParamsTransport, step_params
    from veripen_core.evidence import ReplayEnv, ReplayMode, replay
    from veripen_oracles.oob import LabOob

    app = App(secure=secure)
    srv = LabServer(app).start()
    try:
        rs = RB._routeset(srv.base_url, ())
        transport = NetTransport(
            token_for=make_token, oob=LabOob(app=app, endpoint_value="oob-SAMPLE")
        )
        rt = StepParamsTransport(transport, step_params(cap, baseline_params, pairs))
        res = replay(cap, ReplayEnv(now=0, mode=ReplayMode.LIVE, routeset=rs), rt)
        assert rt.calls, "重放一个请求都没发"
        return res.state.value
    finally:
        srv.stop()


def test_capsule_seals_and_verifies_self_sha() -> None:
    """造得出来、seal 得住：内容哈希可自证。"""
    from veripen_core.evidence import verify_self_sha

    _, result, cap = _run_case("P-01")
    assert cap.capsule_id.startswith("sha256:")
    assert verify_self_sha(cap) is True
    assert cap.route is not None, "1.1 必含 route 块"
    assert cap.route.template_sha256, "route 块必须锚住模板哈希"
    # 证据里不许出现明文模板（27 N5）
    dumped = cap.model_dump_json()
    assert "{id}" not in dumped


def test_multiface_steps_record_their_own_face() -> None:
    """多面 oracle：每一步记**自己的** route_ref。

    早期版本全记主绑定，bola / mass_assignment / xss_stored 的第二面
    在证据里丢失，重放把读请求打到写面上（8 条 VERIFIED 全落 PARTIAL）。
    """
    _, result, cap = _run_case("P-15")  # bola_functional：管理面 + 普通面
    refs = {s.route_ref for s in cap.steps}
    assert len(refs) >= 2, f"多面 oracle 的步骤只记了一个面：{refs}"


def test_verified_finding_replays_ok() -> None:
    """正样本的证据在干净实例上必须重放成功——这是"能复现"的直接证据。"""
    _, result, cap = _run_case("P-01")
    assert result.verdict.value == "VERIFIED"
    assert _replay(cap, result.pairs, result.baseline_params, secure=False) == "OK"


def test_replay_is_not_always_green() -> None:
    """**反向证明**：把目标换成不能满足条件的那一态，重放必须不是 OK。

    没有这条，"复现通过 100%" 只能说明重放永远返回 OK，
    说明不了证据真的被检验过。
    """
    _, result, cap = _run_case("P-01")
    assert _replay(cap, result.pairs, result.baseline_params, secure=True) != "OK"


def test_non_sensitive_headers_are_kept_in_capsule() -> None:
    """载荷型 header（非凭据）必须进证据。

    graphql 的查询放在 ``x-vp-gql`` 里。抹掉它，重放会打出一个"少了载荷的请求"，
    判定自然不复现——那不是网络问题，是证据里缺了当时真的发过的东西。
    """
    _, _result, cap = _run_case("P-25")  # graphql_introspection
    names = {k.lower() for s in cap.steps for k in s.request.headers}
    assert "x-vp-gql" in names, f"载荷 header 没进证据：{names}"


def test_sensitive_headers_are_redacted() -> None:
    """凭据落在证据里必须是 ``<redacted>``（06 §4）。

    这里直接测构造点而不是跑一例：lab 的 token 由传输层 mint（``token_for``），
    不走 oracle 的 header 变体，所以"跑一例"根本产不出 Authorization——
    用真跑会得到一条永远为真的断言，那比没有测试更糟。
    """
    from veripen.bench.capsule import build_capsule
    from veripen_core.evidence import redact_headers, verify_self_sha, seal_capsule_doc

    assert redact_headers({"Authorization": "Bearer secret", "X-Trace": "abc"}) == {
        "Authorization": "<redacted>",
        "X-Trace": "abc",
    }

    # 整份证据里不许出现凭据值
    from types import SimpleNamespace

    from veripen_core.models import Verdict

    binding = SimpleNamespace(
        ref="route://contract/" + "a" * 16, source="contract", sources_all=("contract",),
        confidence=0.9, template="http" + ":" + SEP * 2 + "h" + SEP + "api" + SEP + "x",
        method="GET", param_names=(), template_sha256="t" * 64, ttl_s=900,
    )
    routeset = SimpleNamespace(routeset_sha256="r" * 64, resolver_version="1", asset_id="web-01")
    trace = SimpleNamespace(status=200, body=b"{}")
    pair = SimpleNamespace(
        role="probe", variant=SimpleNamespace(binding_tag=""), baseline=trace, trace=trace,
        sent={}, sent_headers={"Authorization": "Bearer secret", "x-vp-gql": "query"},
    )
    judgement = SimpleNamespace(assertion={}, confidence=0.9)
    cap = seal_capsule_doc(
        build_capsule(
            case_id="X-01", oracle_id="graphql_introspection", oracle_version="1",
            verdict=Verdict.VERIFIED, judgement=judgement,
            pairs=(pair,), baseline_params={}, routeset=routeset,
            route_ref=binding.ref, binding=binding,
        )
    )
    dumped = cap.model_dump_json()
    assert "Bearer secret" not in dumped, "凭据值落进了证据"
    assert "x-vp-gql" in dumped, "载荷 header 被误删"
    assert verify_self_sha(cap) is True


def test_partial_does_not_mean_still_vulnerable() -> None:
    """**钉住一条边界**：``PARTIAL`` 不能读成"漏洞还在"，``OK`` 才是强证据。

    为什么必须钉：oracle 的 judge 靠 body 指纹下结论，而 capsule 只存 body 哈希
    （06 §7）。所以重放只能验"状态形状 + 字节是否完全相同"，验不了"载荷是否仍生效"。
    把 sqli 证据回放到已修复的目标上，状态码仍落在 expected 区间，
    于是得到 ``PARTIAL`` 而不是 ``FAILED``。

    若哪天有人觉得 ``PARTIAL`` 也该算"复现"，这条会红——那正是要拦的读法。
    真正的强证据是 ``OK``（要求逐字节相同），bench 的"复现通过"也只数 ``OK``。
    """
    _, result, cap = _run_case("P-01")
    assert result.verdict.value == "VERIFIED"
    assert _replay(cap, result.pairs, result.baseline_params, secure=True) == "PARTIAL"


def test_refuted_evidence_replays_ok() -> None:
    """**负样本也要能复现**：「查过、确实没有这个洞」是一条结论。

    交付里这占多数（bench 44 例中 14 条 REFUTED）。若它们不能重放，
    "我们验证过你的接口是安全的"这句话就永远无法被审计——
    客户只能选择相信，不能自己复核。
    """
    _, result, cap = _run_case("N-01")  # sqli_bool_diff 负样本（安全态）
    assert result.verdict.value == "REFUTED"
    assert _replay(cap, result.pairs, result.baseline_params, secure=True) == "OK"


def test_refuted_also_can_go_non_ok() -> None:
    """**反向证明**：把"没有洞"的证据回放到有洞的目标上，必须不再 OK。

    没有这条，``test_refuted_evidence_replays_ok`` 通过只能说明
    "重放负样本永远返回 OK"，说明不了它真的在检验。
    """
    _, result, cap = _run_case("N-01")
    assert _replay(cap, result.pairs, result.baseline_params, secure=False) != "OK"


def test_capsule_records_the_real_run_time() -> None:
    """证据上的 ``created_at`` 必须是**运行时的真实时刻**，不能是 1970 占位。

    早先固定成 epoch 是为了"让三轮算出同一个 capsule_id"，但没有任何消费者
    比较跨轮的 capsule_id（``findings_fingerprint`` 明确不含它）。
    用假时间换一个没人用的性质，代价是交付给客户的证据没有可信时间戳。
    """
    import run_bench as RB

    RB.ROUND_CLOCK[0] = "2026-11-02T11:50:00Z"
    _, _r1, cap1 = _run_case("P-01")
    assert cap1.created_at == "2026-11-02T11:50:00Z", cap1.created_at

    RB.ROUND_CLOCK[0] = "2026-11-02T12:00:00Z"
    _, _r2, cap2 = _run_case("P-01")
    assert cap2.created_at == "2026-11-02T12:00:00Z"
    # 时间不同 → capsule_id 不同（这是应该的：证据确实产生于不同时刻）
    assert cap1.capsule_id != cap2.capsule_id
