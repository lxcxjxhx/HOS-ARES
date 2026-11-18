"""认证类 oracle（B411）：O4 —— PKCE 是否真的被校验。

**判据是一条序列**，靠 B410 的多步能力：

1. `authorize`：带 `code_challenge` 取一个授权码，并记下它；
2. `control`：拿这个码 + **正确的** `code_verifier` 换 token —— 必须成功
   （否则这条面根本不做 PKCE，判不了）；
3. `authorize2`：再取一个码；
4. `probe`：拿第二个码 + **错误的** verifier 去换 —— 200 就是**没校验**。

为什么错误 verifier 那一发才是指控：PKCE 的目的就是"**截到授权码也换不出 token**"。
只校验 challenge 存在、不校验 verifier 对上，等于把 PKCE 当装饰。
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Any

from pydantic import BaseModel, ConfigDict

from hos_ares_core.models import ImpactClass, Judgement

from ..auth import checks
from ..common.base import BaseOracle, OracleMeta
from ..common.harness import Pair, SequenceStep, Variant

_SLASH = chr(0x2F)
_AUTHORIZE = _SLASH + "portal" + _SLASH + "authorize"
_TOKEN = _SLASH + "portal" + _SLASH + "token"


def s256(verifier: str) -> str:
    """RFC 7636 的 S256：base64url(sha256(verifier))，去填充。"""
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


class PkceParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repeat: int = 1


class PkceEnforced(BaseOracle):
    """O4：授权码换取时是否校验 code_verifier。"""

    params_model = PkceParams

    meta = OracleMeta(
        id="pkce_verifier",
        version="1.0.0",
        cwe="CWE-287",
        impact=ImpactClass.READ_ONLY,
        severity_rule="pkce_not_enforced",
        selector_tags=("token_issue",),
    )

    def baseline_params(self, binding: Any) -> dict[str, str]:
        return {}

    def baseline_ok(self, trace: Any) -> bool:
        return int(getattr(trace, "status", 0) or 0) != 0

    def plan(self, binding: Any) -> list[Variant]:
        return []

    def sequence(self, binding: Any) -> tuple[SequenceStep, ...]:
        # 每次运行一个新 verifier：写死会让"第二次跑"命中同一个 challenge
        verifier = secrets.token_urlsafe(32)
        self._verifier = verifier
        self._wrong = secrets.token_urlsafe(32)
        # 站内相对地址：安全态会拒外域 redirect_uri，用外域会让对照直接失败
        common = {"redirect_uri": chr(0x2F) + "cb", "state": "s1"}
        return (
            SequenceStep(name="authorize1", role="control", template=_AUTHORIZE,
                         params={**common, "code_challenge": s256(verifier),
                                 "code_challenge_method": "S256"},
                         capture={"code": "body:code"}),
            SequenceStep(name="control", role="control", template=_TOKEN,
                         params={"code": "{{code}}", "code_verifier": verifier}),
            SequenceStep(name="authorize2", role="control", template=_AUTHORIZE,
                         params={**common, "code_challenge": s256(verifier),
                                 "code_challenge_method": "S256"},
                         capture={"code2": "body:code"}),
            SequenceStep(name="probe", role="probe", template=_TOKEN,
                         params={"code": "{{code2}}", "code_verifier": "{{wrong}}"}),
        )

    def judge_sequence(self, traces: dict[str, Any]) -> Judgement:
        authorize1 = traces.get("authorize1")
        control = traces.get("control")
        probe = traces.get("probe")
        if authorize1 is None or control is None or probe is None:
            return self.inconclusive("missing_variant")

        assertion: dict[str, Any] = {
            "kind": "pkce_verifier",
            "expected": {"probe_status": [400, 401, 403]},
            "observed": {
                "authorize1": authorize1.status,
                "control": control.status,
                "probe": probe.status,
            },
            "fingerprint": {"verifier_unchecked": probe.status == 200},
        }
        result = checks.judge_pkce(control_ok=control.status == 200,
                                   probe_status=probe.status)
        assertion["judge"] = result.reason
        if result.verdict == "VERIFIED":
            return self.verified(result.reason, assertion, 0.9)
        if result.verdict == "REFUTED":
            return self.refuted(result.reason, assertion)
        return self.inconclusive(result.reason, assertion)


__all__ = ["PkceEnforced", "s256"]
