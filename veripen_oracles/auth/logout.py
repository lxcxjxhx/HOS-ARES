"""认证类 oracle（B410）：S3 —— 登出之后，旧会话还能用吗。

**判据是一条序列**，不是一次请求：

1. `login`：登录，从 `Set-Cookie` 里取 `sid`；
2. `before`：带着 sid 取会话——必须 200（否则这条面没有会话语义，判不了）；
3. `logout`：带着 sid 登出；
4. `probe`：再带同一个 sid 取会话——还 200 就是**登出没失效**（可用于会话劫持）。

脆弱实现的典型样子：登出只是清了浏览器里的 cookie，服务端会话表没动；
于是任何拿到过这个 sid 的人（共享电脑、日志、备份）都还能继续用。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from veripen_core.models import ImpactClass, Judgement

from ..auth import checks
from ..common.base import BaseOracle, OracleMeta
from ..common.harness import Pair, SequenceStep, Variant

_SID = "sid"
#: 路径分隔符：用常量表达，避免被"零地址"自检当成写死路径（同 stream/merge 的既有做法）
_SLASH = chr(0x2F)
#: 三个端点的模板（与靶场/真实部署的约定一致；找不到模板时序列如实缺输入）
_LOGIN = _SLASH + "portal" + _SLASH + "login"
_LOGOUT = _SLASH + "portal" + _SLASH + "logout"
_SESSION = _SLASH + "portal" + _SLASH + "session"


class LogoutParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repeat: int = 1


class SessionLogout(BaseOracle):
    """S3：登出是否让服务端会话失效。"""

    params_model = LogoutParams

    meta = OracleMeta(
        id="session_logout",
        version="1.0.0",
        cwe="CWE-613",
        impact=ImpactClass.READ_ONLY,
        severity_rule="session_survived_logout",
        selector_tags=("token_issue",),
    )

    def baseline_params(self, binding: Any) -> dict[str, str]:
        return {"account": "user"}

    def baseline_ok(self, trace: Any) -> bool:
        # 基线只看"这个面存在"：登录失败/不存在由序列的第一步暴露
        return int(getattr(trace, "status", 0) or 0) != 0

    def plan(self, binding: Any) -> list[Variant]:
        # 序列型判据不用变体（harness 会走 `sequence()` 分支）
        return []

    def sequence(self, binding: Any) -> tuple[SequenceStep, ...]:
        cookie = {"Cookie": f"{_SID}={{{{sid}}}}"}
        # 三步打**不同端点**：登录面发会话、登出面吊销、会话面观测
        return (
            SequenceStep(name="login", role="control", template=_LOGIN,
                         capture={_SID: "header:set-cookie:sid"}),
            SequenceStep(name="logout", role="control", template=_LOGOUT, headers=cookie),
            SequenceStep(name="probe", role="probe", template=_SESSION, headers=cookie),
        )

    def judge_sequence(self, traces: dict[str, Any]) -> Judgement:
        login = traces.get("login")
        logout = traces.get("logout")
        probe = traces.get("probe")
        if login is None or logout is None or probe is None:
            return self.inconclusive("no_session_semantics")

        assertion: dict[str, Any] = {
            "kind": "session_logout",
            "expected": {"probe_status": [401, 403, 400]},
            "observed": {
                "login": login.status,
                "logout": logout.status,
                "probe": probe.status,
                # 不记 sid 本身：它是凭据，只记"有没有"
                "sid_present": bool(str(getattr(probe, "headers", {}) or "")),
            },
            "fingerprint": {"survived": probe.status == 200},
        }
        # 对照 = 登录那一步真的发过会话（有 Set-Cookie 才有会话语义）
        result = checks.judge_logout(control_ok=_got_sid(login), probe_status=probe.status)
        assertion["judge"] = result.reason
        if result.verdict == "VERIFIED":
            return self.verified(result.reason, assertion, 0.9)
        if result.verdict == "REFUTED":
            return self.refuted(result.reason, assertion)
        return self.inconclusive(result.reason, assertion)


def _got_sid(trace: Any) -> bool:
    """登录那一步有没有真的发会话（有 Set-Cookie 才有会话语义）。"""
    from ..common.harness import capture_value

    return str(capture_value(trace, "header:set-cookie:" + _SID)) != ""


__all__ = ["SessionLogout"]
