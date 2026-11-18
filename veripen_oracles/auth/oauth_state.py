"""认证类 oracle（B389）：OAuth `state` 参数（O2）。

**判据**（CSRF 与授权码注入的前提条件）：
- control：带上一个随机 state → 服务端应把它**原样回传**到回调地址；
- probe：不带 state → 服务端应**拒绝**（没有 state 的授权请求不该被受理）；
- 只要 probe 被受理，就是"state 没被强制"，与"回不回传"无关。

**没做的**："state 可预测"这一半。要判它得连续取两次服务端**自生成**的 state 并比较，
而当前靶场/判定都不自生成（state 由调用方给）。等有自生成的实现再谈——不假装能测。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from veripen_core.models import ImpactClass, Judgement

from ..auth import checks
from ..common.base import BaseOracle, OracleMeta, variant
from ..common.harness import REDIRECT_CODES, Pair, Variant


class OAuthStateParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    #: 站内相对回调（由部署注入，代码里不出现路径字面量）
    safe_target_symbol: str = "SAFE_TARGET"


def _location(trace: Any) -> str:
    for key, value in (getattr(trace, "headers", {}) or {}).items():
        if str(key).lower() == "location":
            return str(value or "")
    return ""


class OAuthState(BaseOracle):
    """O2：授权请求的 `state` 是否被强制与回传。"""

    params_model = OAuthStateParams

    meta = OracleMeta(
        id="oauth_state",
        version="1.0.0",
        cwe="CWE-352",
        impact=ImpactClass.READ_ONLY,
        severity_rule="oauth_state_missing",
        selector_tags=("redirect_entry",),
    )

    def baseline_params(self, binding: Any) -> dict[str, str]:
        first = (binding.param_names or ("redirect_uri",))[0]
        # 基线要带 state：真服务端会要求它，不带就是"畸形请求"，
        # 基线直接 400 → `baseline_abnormal` → 整条判定跑不起来（B389 实测）
        return {first: "{{ref:SAFE_TARGET}}", "state": "{{nonce}}"}

    def baseline_ok(self, trace: Any) -> bool:
        return trace.status in REDIRECT_CODES and bool(_location(trace))

    def plan(self, binding: Any) -> list[Variant]:
        first = (binding.param_names or ("redirect_uri",))[0]
        return [
            # control：带 state，看服务端是否原样回传
            variant("state_control", "control", expect="normal",
                    extra_params={first: "{{ref:SAFE_TARGET}}",
                                  "state": "{{nonce}}"}),
            # probe：把 state 抹掉（`extra_params` 覆盖基线里的同名键——
            # `apply()` 先铺基线再加 extra，所以这里写空串就是"不带"），
            # 看它会不会照样受理
            variant("state_probe", "probe", expect="any_response",
                    extra_params={first: "{{ref:SAFE_TARGET}}", "state": ""}),
        ]

    def judge(self, pairs: list[Pair]) -> Judgement:
        by_name = {p.variant.name: p for p in pairs}
        control = by_name.get("state_control")
        probe = by_name.get("state_probe")
        if control is None or probe is None:
            return self.inconclusive("missing_variant")

        control_redirects = control.trace.status in REDIRECT_CODES
        sent_state = str(dict(control.sent).get("state") or "")
        control_echoed = bool(sent_state) and sent_state in _location(control.trace)
        probe_accepted = probe.trace.status in REDIRECT_CODES

        assertion: dict[str, Any] = {
            "kind": "oauth_state",
            "expected": {"probe_status": [302, 303, 307], "control_status": [302, 303, 307]},
            "observed": {
                "control_redirects": control_redirects,
                "control_echoed": control_echoed,
                # state 原文不进证据：它是防伪票据（与 token 同级别）
                "probe_accepted": probe_accepted,
                "probe_status": probe.trace.status,
            },
            "fingerprint": {"probe_accepted": probe_accepted},
        }
        if control.trace.error or probe.trace.error:
            return self.inconclusive("fetch_failed", assertion)

        result = checks.judge_state_param(
            control_redirects=control_redirects,
            control_echoed=control_echoed,
            probe_accepted=probe_accepted,
        )
        assertion["judge"] = result.reason
        if result.verdict == "VERIFIED":
            return self.verified(result.reason, assertion, 0.9)
        if result.verdict == "REFUTED":
            return self.refuted(result.reason, assertion)
        return self.inconclusive(result.reason, assertion)


__all__ = ["OAuthState"]
