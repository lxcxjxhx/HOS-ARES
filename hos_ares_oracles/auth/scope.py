"""认证类 oracle（B412）：O8 —— 请求超出授权的 scope，服务端会不会**真的授予**。

**判据是一条序列**（B410 的多步能力第三次复用，第三次没再改 harness）：

1. `authorize_read`：以**正常** scope（read）取一个授权码；
2. `token_read`：换 token；
3. `admin_control`：拿它访问需要 admin 的受限资源 —— **必须被拒**；
4. `authorize_wide`：请求 `read admin`（超出授权）取码；
5. `token_wide`：换 token；
6. `admin_probe`：拿它访问同一个受限资源 —— **200 就是 scope 被真的授予了**。

为什么第 3 步不能少：如果正常 scope 的 token 也能进，那不是"提升"，
是「这个资源根本不看 scope」——判定必须退成判不了，而不是把它写成"守住了"。
两者都是 200，区别只在对照组有没有。

安全实现有两种守法的样子，都认：
授权阶段直接拒（第 4 步拿不到码）→ `scope_request_rejected`；
发下来但资源端拦（第 6 步 403）→ `scope_enforced`。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from hos_ares_core.models import ImpactClass, Judgement

from ..auth import checks
from ..common.base import BaseOracle, OracleMeta
from ..common.harness import SequenceStep, Variant

_SLASH = chr(0x2F)
_AUTHORIZE = _SLASH + "portal" + _SLASH + "authorize"
_TOKEN = _SLASH + "portal" + _SLASH + "token"
_ADMIN = _SLASH + "portal" + _SLASH + "admin"


class ScopeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repeat: int = 1
    #: 对照用的正常 scope（这条面上的"应有权限"）
    normal_scope: str = "read"
    #: 探针用的超范围 scope。**要包含 normal 里没有的那一项**，否则探针等于对照
    wide_scope: str = "read admin"


class ScopeEscalation(BaseOracle):
    """O8：超出授权的 scope 是否被授予。"""

    params_model = ScopeParams

    meta = OracleMeta(
        id="scope_escalation",
        version="1.0.0",
        cwe="CWE-269",
        impact=ImpactClass.READ_ONLY,
        severity_rule="scope_not_enforced",
        selector_tags=("token_issue",),
    )

    def baseline_params(self, binding: Any) -> dict[str, str]:
        return {}

    def baseline_ok(self, trace: Any) -> bool:
        # 与 O4 同：基线只看"这个面存在"；真正的语义由序列第 1 步暴露
        return int(getattr(trace, "status", 0) or 0) != 0

    def plan(self, binding: Any) -> list[Variant]:
        return []

    def sequence(self, binding: Any) -> tuple[SequenceStep, ...]:
        # 站内相对回调：安全态会拒外域 redirect_uri，用外域会让第 1 步直接失败
        common = {"redirect_uri": _SLASH + "cb", "state": "s1"}
        return (
            SequenceStep(name="authorize_read", role="control", template=_AUTHORIZE,
                         # params 的静态类型是 BaseModel：扩展字段用 getattr 取
                         params={**common, "scope": getattr(self.params, "normal_scope", "read")},
                         capture={"code1": "body:code"}),
            SequenceStep(name="token_read", role="control", template=_TOKEN,
                         params={"code": "{{code1}}"},
                         capture={"token1": "body:access_token"}),
            SequenceStep(name="admin_control", role="control", template=_ADMIN,
                         headers={"Authorization": "Bearer {{token1}}"}),
            SequenceStep(name="authorize_wide", role="control", template=_AUTHORIZE,
                         params={**common,
                                 "scope": getattr(self.params, "wide_scope", "read admin")},
                         capture={"code2": "body:code"}),
            SequenceStep(name="token_wide", role="control", template=_TOKEN,
                         params={"code": "{{code2}}"},
                         capture={"token2": "body:access_token"}),
            SequenceStep(name="admin_probe", role="probe", template=_ADMIN,
                         headers={"Authorization": "Bearer {{token2}}"}),
        )

    def judge_sequence(self, traces: dict[str, Any]) -> Judgement:
        control = traces.get("admin_control")
        probe = traces.get("admin_probe")
        wide = traces.get("token_wide")
        if control is None or probe is None or wide is None:
            return self.inconclusive("missing_variant")

        control_denied = control.status in (401, 403)
        # 超范围那一跳有没有真的换到 token：授权阶段被拒时这里不是 200
        scope_granted = wide.status == 200
        assertion: dict[str, Any] = {
            "kind": "scope_escalation",
            "expected": {"control_status": [401, 403], "probe_status": [401, 403]},
            "observed": {
                "authorize_wide": getattr(traces.get("authorize_wide"), "status", 0),
                "token_wide": wide.status,
                "control_status": control.status,
                "probe_status": probe.status,
            },
            # 令牌值不进证据（与 O4/S3 一致：它是凭据）
            "fingerprint": {
                "scope_granted": scope_granted,
                "control_denied": control_denied,
                "probe_accepted": probe.status == 200,
            },
        }
        result = checks.judge_scope_escalation(
            control_denied=control_denied,
            scope_granted=scope_granted,
            probe_status=probe.status,
        )
        assertion["judge"] = result.reason
        if result.verdict == "VERIFIED":
            return self.verified(result.reason, assertion, 0.9)
        if result.verdict == "REFUTED":
            return self.refuted(result.reason, assertion)
        return self.inconclusive(result.reason, assertion)


__all__ = ["ScopeEscalation"]
