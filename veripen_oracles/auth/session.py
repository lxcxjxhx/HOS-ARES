"""认证类 oracle（B376）：会话固定 S1。

**为什么先做这一条**：`veripen_oracles/auth/checks.py` 里 5 条判定函数（O1/O5/S1/M1/M7）
从 B9 起只有单测在调，`ALL_ORACLES` 里没有任何 oracle 驱动它们——三条产品路径
（`run` / `hunt` / `platform`）都跑不到。本批把 S1 接成真 oracle，验证
"判定函数 → 可跑 oracle"这条通路，其余 4 条照同一模板铺。

**判据**（`20 §` 会话固定）：
- 我们**自己提供**一个会话 id（`vp-fixed-session`），看服务端是否采纳；
- 采纳 = 登录后拿到的仍是这个 id → `VERIFIED`（攻击者预置的会话延续到已认证状态）；
- 不采纳（换新）→ `REFUTED`；
- 两次都没拿到 `Set-Cookie` → `INCONCLUSIVE:no_session_cookie`（不是"没问题"）。

**标签选择**：用既有的 `token_issue`（"这个面发凭据"），没有新加 `login_entry`——
标签表是 `18 §6` 的冻结字表（`veripen_core/stream.py#SELECTOR_TAGS` 18 个），加一个
就得改规格。会话 cookie 本身就是凭据，归到这条标签名下语义成立；对只发 JWT 的登录面，
本 oracle 会如实落 `INCONCLUSIVE:no_session_cookie`（"没拿到会话 cookie"，不是"没问题"）。

**没做的**：真正的"登录前后对比"（要先拿到服务端发的 id，再带着它去登录）。
那需要一次响应里的值喂给下一次请求，而 harness 的 `plan()` 在基线之后调用、
拿不到基线响应——这个约束本身值得单独记一笔，不在本批硬绕。
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from veripen_core.models import ImpactClass, Judgement

from ..auth import checks
from ..common.base import BaseOracle, OracleMeta, variant
from ..common.harness import Pair, Variant

#: 我们提供的固定 id（**不是**目标自己的 id，也不含地址）
FIXED_SID = "vp-fixed-session"


class SessionParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _cookie_sid(trace: Any) -> str:
    """从 `Set-Cookie` 里取 `sid=…` 的值；没有就是空串。"""
    raw = ""
    for key, value in (getattr(trace, "headers", {}) or {}).items():
        if str(key).lower() == "set-cookie":
            raw = str(value or "")
            break
    for part in raw.split(";"):
        name, _, value = part.strip().partition("=")
        if name.strip().lower() == "sid":
            return value.strip()
    return ""


class SessionFixation(BaseOracle):
    """S1：客户端给的会话 id 被服务端采纳 = 会话固定。"""

    params_model = SessionParams

    meta = OracleMeta(
        id="session_fixation",
        version="1.0.0",
        cwe="CWE-384",
        impact=ImpactClass.READ_ONLY,
        severity_rule="session_fixation",
        selector_tags=("token_issue",),
    )

    def baseline_params(self, binding: Any) -> dict[str, str]:
        return {"account": "user"}

    def plan(self, binding: Any) -> list[Variant]:
        return [
            # control：不带 id 再来一次——证明服务端**会**发新 id（否则无从判断采纳）
            variant("sess_control", "control", expect="normal",
                    extra_params={"vp_again": "1"}),
            # probe：把我们选的 id 交上去，看它是否原样回来
            variant("sess_probe", "probe", expect="normal",
                    extra_params={"sid": FIXED_SID}),
        ]

    def judge(self, pairs: list[Pair]) -> Judgement:
        by_role = {p.role: p for p in pairs}
        probe = by_role.get("probe")
        control = by_role.get("control")
        assertion: dict[str, Any] = {"kind": "session_fixation"}
        if probe is None or control is None:
            return self.inconclusive("missing_variant", assertion)

        probe_sid = _cookie_sid(probe.trace)
        control_sid = _cookie_sid(control.trace)
        assertion["observed"] = {
            # 只记"是不是我们给的那个"，不记 id 原文（会话 id 属于凭据）
            "probe_adopted": probe_sid == FIXED_SID,
            "control_issued": bool(control_sid),
            "probe_status": probe.trace.status,
            "control_status": control.trace.status,
        }
        if probe.trace.error or control.trace.error:
            return self.inconclusive("fetch_failed", assertion)

        # 判定交给 auth/checks 里那条已经验过的纯函数（单一事实源）
        result = checks.judge_session_fixation(before_id=FIXED_SID, after_id=probe_sid)
        assertion["judge"] = result.reason
        if result.verdict == "VERIFIED":
            return self.verified(result.reason, assertion, 0.9)
        if result.verdict == "REFUTED":
            return self.refuted(result.reason, assertion)
        return self.inconclusive(result.reason, assertion)


__all__ = ["FIXED_SID", "SessionFixation"]
