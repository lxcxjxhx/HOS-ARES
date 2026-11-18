"""传输层 oracle（B393）：目标证书的四类缺陷（M2/M3/M4/M5）。

**一条请求、四种结论**：证书有问题时客户端在校验阶段就失败，错误文本本身
就是证据（`CERTIFICATE_VERIFY_FAILED: certificate has expired` 之类）。
所以不需要证书夹具，也不需要新的传输能力——只要把"为什么没连上"读准。

| 码 | 含义 | 客户端看到的文本（OpenSSL） |
|---|---|---|
| M2 | 证书过期 | `certificate has expired` |
| M3 | 不受信（自签 / 链不全） | `self-signed certificate` / `unable to get local issuer` |
| M4 | 名字不匹配 | `hostname mismatch` / `IP address mismatch` |
| M5 | 签名算法太弱 | `md too weak` / `sha1` |

**没做的**：证书只按"客户端拒绝的理由"分类。目标如果用了**客户端信任的**弱证书
（比如本地 CA 签的 SHA1），客户端可能照收——那种情况这里判不出来，
属"信任视角"的固有限制，不是漏判。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from veripen_core.models import ImpactClass, Judgement

from ..auth import checks
from ..common.base import BaseOracle, OracleMeta, variant
from ..common.harness import Pair, Variant


class TlsCertParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repeat: int = 1


class TlsCertificate(BaseOracle):
    """M2/M3/M4/M5：目标证书是否过期/不受信/名字不符/算法太弱。

    选 `protected_read` 而不是 `api_read`：契约解析器**故意丢弃** `api_read`
    （见 `veripen_contract/parser.py` 末段："响应是 JSON 只说明它是 API，不说明能测什么"），
    写进契约也拿不到绑定。这条判定只看 TLS 层，用哪个 https 面都一样（B393 实测）。
    """

    params_model = TlsCertParams
    observes_failures = True

    meta = OracleMeta(
        id="tls_certificate",
        version="1.0.0",
        cwe="CWE-295",
        impact=ImpactClass.READ_ONLY,
        severity_rule="certificate_defect",
        selector_tags=("protected_read",),
    )

    def baseline_params(self, binding: Any) -> dict[str, str]:
        return {}

    def baseline_ok(self, trace: Any) -> bool:
        # 这条判定**就是**在看"为什么连不上"：连接失败也是合法基线
        return True

    def plan(self, binding: Any) -> list[Variant]:
        return [
            variant("cert_again", "control", expect="any_outcome"),
            variant("cert_probe", "probe", expect="any_outcome"),
        ]

    def judge(self, pairs: list[Pair]) -> Judgement:
        by_role = {p.role: p for p in pairs}
        probe = by_role.get("probe")
        if probe is None:
            return self.inconclusive("missing_variant")

        trace = probe.trace
        reason_text = str(getattr(trace, "error", "") or "")
        assertion: dict[str, Any] = {
            "kind": "tls_certificate",
            # 这条判据不看状态码（它读的是"为什么没连上"），结论谓词由 fingerprint 声明
            "expected": {},
            "observed": {
                "scheme": str(getattr(trace, "scheme", "") or ""),
                # 错误原文要留：它是"哪一类证书缺陷"的唯一证据
                "error": reason_text[:160],
                "status": trace.status,
            },
            "fingerprint": {"cert_defect": bool(reason_text)},
        }
        result = checks.judge_certificate_defect(
            error_reason=reason_text, scheme=str(getattr(trace, "scheme", "") or "")
        )
        assertion["judge"] = result.reason
        if result.verdict == "VERIFIED":
            return self.verified(result.reason, assertion, 0.9)
        if result.verdict == "REFUTED":
            return self.refuted(result.reason, assertion)
        return self.inconclusive(result.reason, assertion)


__all__ = ["TlsCertificate"]
