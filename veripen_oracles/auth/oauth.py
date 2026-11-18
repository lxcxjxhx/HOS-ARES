"""认证类 oracle（B377）：授权码重放 O5。

**和 S1 同一条通路**：`auth/checks.py` 里的 `judge_code_replay` 从 B9 起只有单测在调，
本批把它接成能跑的 oracle——验证"判定函数 → 产品路径"这套模板对第二条也成立。

**判据**（`20 §3` 取证纪律：两次都要 trace）：
- 第一次换码成功、**第二次**也成功 → `VERIFIED:authorization_code_replayable`
  （码可复用；攻击者截获一次就能长期换 token）；
- 第二次被拒 → `REFUTED:code_single_use`（一次性）；
- 第一次就没成功 → `INCONCLUSIVE:first_exchange_failed`（**不是**"没问题"）。

**变体顺序**：harness 把 control 排在前面先发，所以 control = 第一次换码、
probe = 第二次换码（同一个码）。没有第二条"对照"可做——这个判据本身就是两次同请求。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from veripen_core.models import ImpactClass, Judgement

from ..auth import checks
from ..common.base import BaseOracle, OracleMeta, variant
from ..common.harness import Pair, Variant

#: 我们用的授权码（固定值：要让第二发能命中"同一个码"）。
#: **必须是目标能接受的形状**（16 位十六进制）：靶场的安全态会做形式校验，
#: 用 `vp-fixed-code` 这种串会先被 400 掉，判定直接落 `baseline_abnormal`（B391 实测）。
FIXED_CODE = "00000000000000ff"
#: 基线用的码（不参与"能不能换第二次"的判定）
BASELINE_CODE = "00000000000000ee"


class CodeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    #: 这条判定**天生不幂等**：同一个码第一次 200、第二次才见分晓。
    #: harness 默认每个变体发两次（repeats=2），第二次会把"一次性"读成"结果不稳定"
    #: （实测：安全靶落 INCONCLUSIVE:unstable）。所以这里显式声明只发一次。
    repeat: int = 1


class CodeReplay(BaseOracle):
    """O5：同一个授权码能换第二次 = 可重放。"""

    params_model = CodeParams

    meta = OracleMeta(
        id="code_replay",
        version="1.0.0",
        cwe="CWE-294",
        impact=ImpactClass.READ_ONLY,
        severity_rule="code_replay",
        selector_tags=("token_issue",),
    )

    def baseline_ok(self, trace: Any) -> bool:
        """基线必须是**真的换码接口**（响应里有 access_token）。

        B390 实测：`token_issue` 这个标签同时覆盖登录面与换码面，于是本 oracle 会对着
        `/portal/login` 发两次 `code=`——登录面把未知参数一扔就 200，判定于是得出
        「授权码可重放」。那是纯误报：那个面压根没有换码语义。
        """
        if not trace.is_normal():
            return False
        return b"access_token" in (getattr(trace, "body", b"") or b"")

    def baseline_params(self, binding: Any) -> dict[str, str]:
        # 基线用**另一个**码：一次性语义下，基线若也消费 FIXED_CODE，
        # 后面两发必然都 400，判成 `first_exchange_failed`——那是把自己的探针
        # 算进了第一次换码。基线只负责"这个面通不通"。
        return {"code": BASELINE_CODE}

    def plan(self, binding: Any) -> list[Variant]:
        # control 先发（第一次换码），probe 后发（同一个码再换一次）
        return [
            variant("code_first", "control", expect="normal",
                    extra_params={"code": FIXED_CODE}),
            variant("code_second", "probe", expect="normal",
                    extra_params={"code": FIXED_CODE}),
        ]

    def judge(self, pairs: list[Pair]) -> Judgement:
        by_role = {p.role: p for p in pairs}
        first = by_role.get("control")
        second = by_role.get("probe")
        assertion: dict[str, Any] = {"kind": "authorization_code_replay"}
        if first is None or second is None:
            return self.inconclusive("missing_variant", assertion)

        assertion["observed"] = {
            "first_status": first.trace.status,
            "second_status": second.trace.status,
            "first_ok": first.trace.is_normal(),
            "second_ok": second.trace.is_normal(),
        }
        if first.trace.error or second.trace.error:
            return self.inconclusive("fetch_failed", assertion)

        result = checks.judge_code_replay(first_ok=first.trace.is_normal(),
                                          second_ok=second.trace.is_normal())
        assertion["judge"] = result.reason
        if result.verdict == "VERIFIED":
            return self.verified(result.reason, assertion, 0.9)
        if result.verdict == "REFUTED":
            return self.refuted(result.reason, assertion)
        return self.inconclusive(result.reason, assertion)


__all__ = ["FIXED_CODE", "CodeReplay"]
