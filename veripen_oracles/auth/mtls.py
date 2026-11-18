"""认证类 oracle（B385）：服务端是否真的要求客户端证书（M1）。

**判据不是差分型**：它问的是"不带证书能不能连上"，一次观测就够。所以：
- `baseline_ok` 放宽为"跑成 **或** 连接/TLS 层失败"——这道面的基线本来就可能是"连不上"；
- control 用 `expect="normal"`（对照不承担差分职责）；
- 只有 VERIFIED 会被 `three_way_holds` 拦，而它要求"两次都跑成且稳定"——正好是
  "不带证书被接受"该有的形态。

**M7（服务端压根没请求证书）在客户端侧测不出来**：Python 的 `ssl` 不暴露
CertificateRequest，是否能从"握手成功"反推"没请求"取决于协议版本（TLS 1.3 下
可选请求与不请求都握手成功）。所以本 oracle 只出 M1 的结论，M7 保持清单态。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from veripen_core.models import ImpactClass, Judgement

from ..auth import checks
from ..common.base import BaseOracle, OracleMeta, variant
from ..common.harness import Pair, Variant

#: 判定码里用来识别"服务端要求证书"的片段（OpenSSL 原文，大小写不敏感比较）
_CERT_REQUIRED = "certificate required"
_UNKNOWN_CA = "unknown ca"


class MtlsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repeat: int = 1
    #: 这个面**是否被声明为 mTLS 保护**。默认 False：不声明就不出结论。
    #: 理由——"服务端没要求客户端证书"对一个普通 API 是正常的，只有部署说
    #: "这里应该是双向认证"时，它才是缺陷。宁可少判，不可假判。
    expect_mtls: bool = False


class MtlsRequired(BaseOracle):
    """M1：不带客户端证书也能连上 → 这个面并没有做双向认证。

    **已知边界**：选 `protected_read` 时基线是 401，而 `three_way_holds` 要求基线
    "正常或 3xx"——于是即使声明了 `expect_mtls`，这条面上也只能落
    `three_way_failed`（B386 实测）。要出 VERIFIED 需要一个 200 的 https 面
    （单测里用假 trace 覆盖了那条分支）。
    """

    params_model = MtlsParams
    observes_failures = True
    #: 授权面：不带凭据的基线本来就该是 401/403（B387）
    baseline_accepts_denied = True

    meta = OracleMeta(
        id="mtls_required",
        version="1.0.0",
        cwe="CWE-295",
        impact=ImpactClass.READ_ONLY,
        severity_rule="mtls_not_required",
        selector_tags=("protected_read",),
    )

    def baseline_params(self, binding: Any) -> dict[str, str]:
        return {}

    def baseline_ok(self, trace: Any) -> bool:
        # 这条面的基线可以是"连不上"：客户端证书缺失时握手本就该失败
        return trace.error is not None or bool(trace.is_normal()) or trace.status in (401, 403)

    def plan(self, binding: Any) -> list[Variant]:
        return [
            # `any_response`：这条判据问的是"TLS 层放不放行"，401/403 也是有效观测。
            # 用 "normal" 的话，授权面上 probe 永远不达标 → three_way 失败（B387 实测）
            variant("mtls_again", "control", expect="normal"),
            variant("mtls_probe", "probe", expect="any_response"),
        ]

    def judge(self, pairs: list[Pair]) -> Judgement:
        by_role = {p.role: p for p in pairs}
        probe = by_role.get("probe")
        if probe is None:
            return self.inconclusive("missing_variant")

        trace = probe.trace
        # http 面谈不上 mTLS：诚实地说"不适用"，不判否
        if str(getattr(trace, "scheme", "") or "") != "https":
            return self.inconclusive("not_https", {"scheme": getattr(trace, "scheme", "")})

        handshake_ok = trace.error is None and trace.status != 0
        reason_text = str(trace.error or "")
        assertion: dict[str, Any] = {
            "kind": "mtls_required",
            "expected": {"probe_status": [200, 302, 401, 403]},
            "observed": {
                "handshake_ok": handshake_ok,
                # 错误原文要留：它是"为什么连不上"的唯一证据（20 §4）
                "error": reason_text[:120],
                "status": trace.status,
            },
            "fingerprint": {"handshake_ok": handshake_ok},
        }
        if not getattr(self.params, "expect_mtls", False):
            # 没有声明就不判：把"普通 API 没要求客户端证书"记成高危是纯误报
            return self.inconclusive("mtls_not_declared", assertion)

        result = checks.judge_mtls_handshake(
            handshake_ok=handshake_ok,
            error_reason=reason_text or None,
            client_cert_requested=_CERT_REQUIRED in reason_text.lower(),
        )
        assertion["judge"] = result.reason
        if result.verdict == "VERIFIED":
            return self.verified(result.reason, assertion, 0.9)
        if result.verdict == "REFUTED":
            return self.refuted(result.reason, assertion)
        return self.inconclusive(result.reason, assertion)


__all__ = ["MtlsRequired"]
