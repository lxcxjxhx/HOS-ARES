"""认证类 oracle（B390）：会话 Cookie 的安全标志（S2）。

**判据**只看 `Set-Cookie` 的属性，不看值：
- `HttpOnly` 缺 → 脚本可读，XSS 直接升格为会话劫持；
- `Secure` 缺（且是 https）→ 同域明文请求会带上它；
- `SameSite` 缺或 `None` → 跨站请求会带上它（CSRF 面变大）。

三条里任何一条缺 → 成立（`cookie_flags_missing`），并把缺的项写进证据；
全齐 → 判否；**根本没有 Set-Cookie** → 判不了（不是"没问题"）。

Cookie 的**值不进证据**（会话 id 属凭据，`redact_headers` 会把 `set-cookie` 整体脱敏），
所以本 oracle 的结论只能由"响应头里有哪些属性"重算——这也是退而求其次的取舍：
证据能证明"当时确实缺 HttpOnly"，但无法重放那个 cookie 本身。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from hos_ares_core.models import ImpactClass, Judgement

from ..auth import checks
from ..common.base import BaseOracle, OracleMeta, variant
from ..common.harness import Pair, Variant

_FLAGS = ("httponly", "secure", "samesite")


class CookieParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    #: 这个面是不是走 https（Secure 标志只在 https 上才有意义）
    https: bool = False


def _set_cookie(trace: Any) -> str:
    for key, value in (getattr(trace, "headers", {}) or {}).items():
        if str(key).lower() == "set-cookie":
            return str(value or "")
    return ""


class CookieFlags(BaseOracle):
    """S2：会话 Cookie 是否带齐 HttpOnly / Secure / SameSite。"""

    params_model = CookieParams

    meta = OracleMeta(
        id="cookie_flags",
        version="1.0.0",
        cwe="CWE-1004",
        impact=ImpactClass.READ_ONLY,
        severity_rule="cookie_flags_missing",
        selector_tags=("token_issue",),
    )

    def baseline_params(self, binding: Any) -> dict[str, str]:
        return {}

    def baseline_ok(self, trace: Any) -> bool:
        # 基线只要求"这个面确实发了 cookie"——是不是安全标志齐全由判定负责
        return bool(_set_cookie(trace))

    def plan(self, binding: Any) -> list[Variant]:
        return [
            variant("cookie_again", "control", expect="any_response"),
            variant("cookie_probe", "probe", expect="any_response"),
        ]

    def judge(self, pairs: list[Pair]) -> Judgement:
        by_role = {p.role: p for p in pairs}
        probe = by_role.get("probe")
        control = by_role.get("control")
        if probe is None or control is None:
            return self.inconclusive("missing_variant")

        raw = _set_cookie(probe.trace) or _set_cookie(control.trace)
        assertion: dict[str, Any] = {"kind": "cookie_flags", "expected": {}}
        if not raw:
            return self.inconclusive("no_set_cookie", assertion)

        lowered = raw.lower()
        present = {flag: flag in lowered for flag in _FLAGS}
        # SameSite=None 等于没设：跨站照样带上
        if present["samesite"] and "samesite=none" in lowered:
            present["samesite"] = False
        # Secure 只在 https 上要求；http 目标上缺它不是缺陷
        https = bool(getattr(self.params, "https", False)) or str(
            getattr(probe.trace, "scheme", "") or ""
        ) == "https"
        required = {
            "httponly": present["httponly"],
            "secure": present["secure"] if https else True,
            "samesite": present["samesite"],
        }
        missing = sorted(name for name, ok in required.items() if not ok)
        assertion["observed"] = {
            "missing": missing,
            # 只留属性名，不留值：cookie 值属凭据
            "attributes": sorted({p.split("=")[0].strip().lower()
                                  for p in raw.split(";")[1:]}),
            "https": https,
        }
        assertion["expected"] = {"cookie_flags_complete": [0]}
        assertion["fingerprint"] = {"flags_missing": bool(missing)}
        if not missing:
            assertion["expected"] = {"cookie_flags_complete": [1]}
        result = checks.judge_cookie_flags(missing=tuple(missing), https=https)
        assertion["judge"] = result.reason
        if result.verdict == "VERIFIED":
            return self.verified(result.reason, assertion, 0.9)
        if result.verdict == "REFUTED":
            return self.refuted(result.reason, assertion)
        return self.inconclusive(result.reason, assertion)


__all__ = ["CookieFlags"]
