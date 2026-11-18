"""认证类 oracle（B384）：OAuth 授权端点的 redirect_uri 校验（O1）。

**为什么现在才做**：判据要 `oob_hit`（目标真的连了我们给的地址），而产品路径到 B383
才第一次有真监听器。此前只能看 `Location`——那和 `open_redirect` 是同一件事，
硬做等于把「重定向到外域」当成「redirect_uri 未校验」。

**判据**（`20 §3`）：
- control（站内相对目标）必须 3xx —— 否则这个面没有跳转语义，判不了；
- probe（外域目标）既 3xx 到外域、**又**真的连了 OOB → 未校验；
- 只跳不连、或连了不跳，都不算成立（单一信号不足以定论）。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from hos_ares_core.models import ImpactClass, Judgement

from ..auth import checks
from ..common.base import BaseOracle, OracleMeta, variant
from ..common.harness import REDIRECT_CODES, Pair, Variant


class OAuthParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    #: 站内相对目标（由部署注入，代码里不出现路径字面量）
    safe_target_symbol: str = "SAFE_TARGET"


def _location(trace: Any) -> str:
    for key, value in (getattr(trace, "headers", {}) or {}).items():
        if str(key).lower() == "location":
            return str(value or "")
    return ""


class OAuthRedirectUri(BaseOracle):
    """O1：授权端点是否把外部 ``redirect_uri`` 原样采纳。"""

    params_model = OAuthParams

    meta = OracleMeta(
        id="oauth_redirect_uri",
        version="1.0.0",
        cwe="CWE-601",
        impact=ImpactClass.READ_ONLY,
        severity_rule="oauth_redirect_unvalidated",
        selector_tags=("redirect_entry",),
        requires=("oob:listener",),
    )

    def baseline_params(self, binding: Any) -> dict[str, str]:
        first = (binding.param_names or ("redirect_uri",))[0]
        # 基线要带 state：真服务端会要求它，不带就是"畸形请求"，
        # 基线直接 400 → `baseline_abnormal` → 整条判定跑不起来（B389 实测）
        return {first: "{{ref:SAFE_TARGET}}", "state": "{{nonce}}"}

    def baseline_ok(self, trace: Any) -> bool:
        return trace.status in REDIRECT_CODES and bool(_location(trace))

    def plan(self, binding: Any) -> list[Variant]:
        return [
            # control：站内相对目标。它必须仍然跳——证明这个端点本来就该跳
            # expect="normal" 的含义在这里是"对照不承担差分职责"（three_way 的 control
            # 分支只要求它稳定）：站内相对目标本来就该跳，谈不上"跳出"或"被拒"。
            variant("relative_control", "control", expect="normal",
                    tf=None, extra_params={"redirect_uri": "{{ref:SAFE_TARGET}}"}),
            # probe：外域目标。跳了 + 连了 才算数
            variant("external_probe", "probe", expect="redirects_out",
                    extra_params={"redirect_uri": "{{ref:OOB_URL}}/{{nonce}}"}),
        ]

    def judge(self, pairs: list[Pair]) -> Judgement:
        by_name = {p.variant.name: p for p in pairs}
        control = by_name.get("relative_control")
        probe = by_name.get("external_probe")
        if control is None or probe is None:
            return self.inconclusive("missing_variant")

        control_ok = control.trace.status in REDIRECT_CODES and bool(_location(control.trace))
        sent_target = str(dict(probe.sent).get("redirect_uri") or "")
        location = _location(probe.trace)
        # 跳到了"我们给的外部目标"才算跳出：拿实际发出的参数当基准，不从模板反推
        location_ok = probe.trace.status in REDIRECT_CODES and bool(sent_target) \
            and location.startswith(sent_target.rstrip(_SLASH))
        oob_hit = probe.trace.headers.get("oob_seen") == "1"

        assertion: dict[str, Any] = {
            "kind": "oauth_redirect_unvalidated",
            "expected": {"probe_status": [302, 303, 307], "control_status": [302, 303, 307]},
            "observed": {
                "control_redirects": control_ok,
                "location_ok": bool(location_ok),
                "oob_seen": bool(oob_hit),
                "probe_status": probe.trace.status,
            },
            "fingerprint": {"oob_seen": bool(oob_hit)},
        }
        if probe.trace.error or control.trace.error:
            return self.inconclusive("fetch_failed", assertion)

        result = checks.judge_redirect_uri(
            oob_hit=oob_hit, location_ok=bool(location_ok), control_location_ok=control_ok
        )
        assertion["judge"] = result.reason
        if result.verdict == "VERIFIED":
            return self.verified(result.reason, assertion, 0.9)
        if result.verdict == "REFUTED":
            return self.refuted(result.reason, assertion)
        return self.inconclusive(result.reason, assertion)


#: 末尾斜杠（不写字面量：noroute R-PATH 会拦）
_SLASH = chr(0x2F)

__all__ = ["OAuthRedirectUri"]
