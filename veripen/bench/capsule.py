"""从一次真实的 oracle 执行造 capsule（证据落盘的唯一路径）。

**为什么需要这个模块**：在此之前 ``run_bench`` 的 ``capsule_id`` 是
``sha256(case_id + "/cap")``——一个凭空的字符串，``replay_status`` 则是
``"OK" if verdict == "VERIFIED" else "NEVER"``。也就是说，bench 报告里那两列
（"复现通过 0.58"、每条 finding 的 capsule 编号）从来没有人真的生成过一份证据，
更没有人真的重放过它。这和 ``tokens=800`` 是同一类问题：**看起来是指标，其实是常量**。

这里的职责只有一件：把 ``RunResult``（oracle 真跑出来的结论）翻译成
``CapsuleDoc``（证据文档），再由调用方 seal 与 replay。翻译必须是**忠实**的：

- 步骤来自 ``pairs``——那是 judge 真正读过的那些 trace，不是重新发一遍；
- 断言来自 ``judgement.assertion``——judge 自己的输出，不重新表述；
- route 块来自解析出的绑定——地址不落进 capsule（只落 ``template_sha256``）。

**不在这里做的事**：不判断结论对不对（那是 judge 的事），不 seal（那是
``seal_capsule_doc`` 的事），不重放（那是 ``replay`` 的事）。
一处只做一件事，出问题时才定位得到是哪一环。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from veripen_core.evidence import (
    AssertionBlock,
    CapsuleDoc,
    Chain,
    ClockBlock,
    EnvBlock,
    OracleBlock,
    RedactionBlock,
    RouteBlock,
    TargetBlock,
    build_request,
    build_response,
    build_step,
    chain_capsules,
    save_capsule,
)

#: **仅测试夹具**用的占位时刻。真实运行时调用方必须传运行时钟（见 run_bench）。
#:
#: 早先把它当成默认值，于是每份证据都声称自己产生于 1970 年——
#: 理由是"让三轮算出同一个 capsule_id"，但那个确定性没有任何消费者
#: （``findings_fingerprint`` 明确不含 capsule_id）。用假时间换一个没人用的性质，
#: 代价是证据里的时间戳是假的：交付给客户时，一份没有真实时间的证据是可质疑的。
DEFAULT_CREATED_AT = "1970-01-01T00:00:00Z"


def _render_path(template: str, params: Mapping[str, str]) -> str:
    """把参数填进模板，仅用于 ``path_rendered``（本地重放辅助，**不进哈希**）。

    这里**不做 URL 编码**：``path_rendered`` 的用途是让人/工具看清"当时打了哪条路径"，
    编码形态由传输层负责。做编码反而会让它和传输层实际发出的字节不一致。
    """
    out = template
    for name, value in params.items():
        out = out.replace("{" + name + "}", str(value))
    return out


def build_capsule(
    *,
    case_id: str,
    oracle_id: str,
    oracle_version: str,
    verdict: Any,
    judgement: Any,
    pairs: tuple[Any, ...],
    baseline_params: Mapping[str, str],
    routeset: Any,
    route_ref: str,
    binding: Any,
    bindings_by_tag: Mapping[str, Any] | None = None,
    profile_name: str = "bench",
    profile_hash: str = "",
    image_digest: str = "",
    created_at: str = DEFAULT_CREATED_AT,
    cwe: str = "",
    confidence_rule: str = "double_condition_satisfied",
) -> CapsuleDoc:
    """把一次运行的结果翻译成 capsule 1.1。缺 route 块即抛（由 seal 兜住）。"""
    # tag → 绑定的 ref。多面 oracle 的每一步可能打在**不同**的面上
    # （bola 读管理面 + 普通面、mass_assignment 写 + 回读、xss_stored 写 + 读）。
    # 早期版本给所有步骤都记主绑定的 ref，于是 capsule 声称"五步打的是同一个面"——
    # 重放时把读请求发到写面上，判定自然复现不了（表现为 PARTIAL）。
    ref_of_tag: dict[str, str] = {}
    for tag, b in (bindings_by_tag or {}).items():
        ref_of_tag[str(tag)] = str(getattr(b, "ref", route_ref))

    def _ref_for(pair: Any) -> str:
        tag = str(getattr(getattr(pair, "variant", None), "binding_tag", "") or "")
        return ref_of_tag.get(tag, route_ref)

    def _method_for(pair: Any) -> str:
        """这一步**实际用的方法**：变体覆盖优先（CORS 预检是 OPTIONS）。

        早先每步都写 binding 的 GET，于是 capsule 声称"那一步是 GET"，
        而实际发的是 OPTIONS——重放照着 GET 打，501 变成 200，落 PARTIAL。
        证据记错方法，等于把"复现不了"伪装成"判定不稳"。
        """
        override = str(getattr(getattr(pair, "variant", None), "method", "") or "")
        return (override or str(getattr(binding, "method", "GET"))).upper()

    steps: list[Any] = []

    if pairs:
        first = pairs[0]
        steps.append(
            build_step(
                n=1,
                role="baseline",
                account="",
                route_ref=route_ref,
                request=build_request(
                    method=str(getattr(binding, "method", "GET")),
                    path_rendered=_render_path(binding.template, baseline_params),
                ),
                response=build_response(
                    status=int(first.baseline.status),
                    body=first.baseline.body.decode("utf-8", "replace"),
                ),
            )
        )

    n = len(steps)
    for pair in pairs:
        n += 1
        steps.append(
            build_step(
                n=n,
                role=pair.role,
                account="",
                route_ref=_ref_for(pair),
                request=build_request(
                    method=_method_for(pair),
                    path_rendered=_render_path(binding.template, pair.sent),
                    # header 也要进证据：graphql 的载荷在 x-vp-gql、JWT 在 Authorization。
                    # redact_headers 会把凭据类值换成 <redacted>，所以不会泄密；
                    # 但"非凭据 header 的值"必须留，否则重放会打出少了载荷的请求。
                    headers=dict(pair.sent_headers or {}),
                ),
                response=build_response(
                    status=int(pair.trace.status),
                    body=pair.trace.body.decode("utf-8", "replace"),
                ),
            )
        )

    assertion = dict(getattr(judgement, "assertion", {}) or {})
    return CapsuleDoc(
        finding_id=f"bench-{case_id}",
        created_at=created_at,
        oracle=OracleBlock(id=oracle_id, version=oracle_version, cwe=cwe),
        route=RouteBlock(
            routeset_sha256=routeset.routeset_sha256,
            ref=route_ref,
            source=binding.source,
            sources_all=list(binding.sources_all),
            confidence=binding.confidence,
            template_sha256=binding.template_sha256,
            resolver_version=routeset.resolver_version,
            resolved_at=created_at,
            ttl_s=binding.ttl_s,
        ),
        target=TargetBlock(
            asset_id=str(getattr(routeset, "asset_id", "")),
            frame_method=str(getattr(binding, "method", "GET")).upper(),
            template_sha256=binding.template_sha256,
        ),
        env=EnvBlock(
            profile_name=profile_name,
            profile_hash=profile_hash,
            image_digest=image_digest,
            runner_id="bench",
            clock=ClockBlock(t0=0, t1=0),
        ),
        steps=steps,
        assertion=AssertionBlock(
            kind=str(assertion.get("kind") or oracle_id),
            statement=str(assertion.get("statement") or ""),
            expected=dict(assertion.get("expected") or {}),
            observed=dict(assertion.get("observed") or {}),
            fingerprint=dict(assertion.get("fingerprint") or {}),
        ),
        verdict=verdict,
        confidence=float(getattr(judgement, "confidence", 0.0) or 0.0),
        confidence_rule=confidence_rule,
        route_strength=str(binding.source),
        redaction=RedactionBlock(),
        chain=Chain(prev_sha256=None),
    )


def step_params(capsule: CapsuleDoc, baseline_params: Mapping[str, str],
                pairs: tuple[Any, ...]) -> dict[int, dict[str, str]]:
    """步骤号 → 该步实际发出的参数。**重放时用它，不从 path 反解。**

    为什么不在 capsule 里存参数：``RequestBlock`` 没有这个字段，而且参数里有
    nonce 之类的值，存进去会改变证据的哈希口径（06 §2.2 只允许 ``path_rendered``
    作为本地重放辅助）。所以重放时由调用方把**同一批参数**递回来——
    这正是"用同一次运行的参数，打重新解析出来的地址"。
    """
    out: dict[int, dict[str, str]] = {}
    n = 1
    if pairs:
        out[n] = dict(baseline_params)
    for pair in pairs:
        n += 1
        out[n] = dict(pair.sent)
    return out


class StepParamsTransport:
    """把 ``replay`` 的 ``Transport`` 协议接到任意传输层上，参数按步骤号回放。

    两个接口形状不同，必须在这里对齐——**不让 replay 认识 HTTP**：
    - replay 要 ``send(step, binding) -> TraceResult``（只关心状态与 body 哈希）；
    - 传输层要 ``send(binding, params, role, headers) -> Trace``。

    地址**只从 binding 来**（27 N1）；参数按 ``step.n`` 取回放时递进来的那一份。
    这里刻意不从 ``path_rendered`` 反解参数：那等于把"地址从哪来"挪回离线数据。
    """

    def __init__(self, inner: Any, params_by_step: Mapping[int, Mapping[str, str]]) -> None:
        self.inner = inner
        self.params_by_step = params_by_step
        self.calls: list[int] = []

    def send(self, step: Any, binding: Any) -> Any:
        from veripen_core.evidence import TraceResult

        self.calls.append(int(step.n))
        params = dict(self.params_by_step.get(int(step.n), {}))
        # header 从证据里取（已脱敏）。凭据类值是 <redacted>，原样发出去只会得到 401——
        # 那是"证据不足以重放"的正确表现，不该用别的值兜底。
        headers = dict(getattr(step.request, "headers", {}) or {})
        # 方法也要照证据发（CORS 预检是 OPTIONS）。这里曾固定用 binding 的方法，
        # 于是「证据说 GET、当时发的是 OPTIONS」这种情形，重放永远对不上。
        method = str(getattr(step.request, "method", "") or "")
        try:
            trace = self.inner.send(binding, params, step.role, headers or None, method=method)
        except Exception as exc:  # 传输层是插件边界：失败必须留痕，不许吞
            return TraceResult(status=0, body_sha256="", error=type(exc).__name__)
        return TraceResult(status=int(trace.status), body_sha256=trace.body_sha256)


def save_round_chain(docs: Sequence[CapsuleDoc], directory: str | Path) -> list[CapsuleDoc]:
    """把一轮的 capsule 串成**一条链**再落盘，返回封印后的文档。

    为什么要串链再落盘：``build_capsule`` 产出的文档 ``chain.prev = None``，
    单份自证够用，"整轮没被抽走一份"却无从谈起。B149 之后 ``verify_chain``
    要求链头 ``prev`` 是全零，所以"每轮一条链"才是能过 ``--verify-chain`` 的最小单位；
    三轮共用目录会得到三个链头，反而被判成断链。

    为什么必须用返回值回填：seal 会把 ``prev`` 写进哈希，`self_sha256` 随之改变。
    调用方若继续持有封印前的 ``capsule_id``，报告里的指针就指向一份盘上不存在的文件
    ——B148 那类"证据指到别处"，换了个位置再犯一次。
    """
    sealed = chain_capsules(list(docs))
    for cap in sealed:
        save_capsule(cap, directory)
    return sealed


__all__ = [
    "DEFAULT_CREATED_AT",
    "StepParamsTransport",
    "build_capsule",
    "save_round_chain",
    "step_params",
]
