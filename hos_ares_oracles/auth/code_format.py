"""认证类 oracle（B391）：授权码**形式校验**（O7）。

问的是：换码端点会不会先看码长什么样，再决定要不要处理。

- 脆弱实现：任何非空字符串都往下走 → 攻击面变大（可爆破/可注入/可枚举）；
- 正确实现：先按形状过滤（长度、字符集），畸形码一步就拒。

**判据**：只要有一支畸形码被受理 → `code_format_unchecked`（并把被受理的形状点名）；
全部被拒 → `code_format_checked`。**形状一律不写进证据的"值"**，只写标签
（`too_short` 之类），因为畸形码本身可能被当成凭据。

**没做的**：不判"合法形状的取值范围够不够大"（那要估计熵），也不判"已用过的码"——
后者是 O5（`code_replay`）的职责。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from hos_ares_core.models import ImpactClass, Judgement

from ..auth import checks
from ..common.base import BaseOracle, OracleMeta, variant
from ..common.harness import Pair, Variant

#: 基线用的合法码（16 位十六进制；与靶场的格式约定一致）
VALID_CODE = "0123456789abcdef"

#: 畸形码：**只写形状名与值**，值本身就是畸形输入，不含真实凭据
MALFORMED: tuple[tuple[str, str], ...] = (
    ("too_short", "x"),
    ("too_long", "a" * 400),
    ("illegal_chars", "code!!?"),
    ("wrong_case", "ABCDEF0123456789"),
    ("sql_ish", "' OR 1=1 --"),
)


class CodeFormatParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repeat: int = 1


class CodeFormat(BaseOracle):
    """O7：换码端点是否校验授权码的形式。"""

    params_model = CodeFormatParams

    meta = OracleMeta(
        id="code_format",
        version="1.0.0",
        cwe="CWE-20",
        impact=ImpactClass.READ_ONLY,
        severity_rule="code_format_unchecked",
        selector_tags=("token_issue",),
    )

    def baseline_ok(self, trace: Any) -> bool:
        # 与 `code_replay` 同一条纪律：基线必须是**真的换码接口**
        # （登录面把未知参数一扔就 200，会被误当成"什么码都收"）
        if not trace.is_normal():
            return False
        return b"access_token" in (getattr(trace, "body", b"") or b"")

    def baseline_params(self, binding: Any) -> dict[str, str]:
        return {"code": VALID_CODE}

    def plan(self, binding: Any) -> list[Variant]:
        variants = [
            # control：合法码再换一次（证明这个面本身工作正常）
            variant("format_control", "control", expect="any_response",
                    extra_params={"code": VALID_CODE}),
        ]
        for label, value in MALFORMED:
            variants.append(
                variant(f"format_{label}", "probe", expect="any_response",
                        extra_params={"code": value})
            )
        return variants

    def judge(self, pairs: list[Pair]) -> Judgement:
        probes = [p for p in pairs if p.role == "probe"]
        if not probes:
            return self.inconclusive("missing_variant")

        accepted: list[str] = []
        for pair in probes:
            label = pair.variant.name.removeprefix("format_")
            # "被受理"= 2xx 且真的给了 token：只回 200 不给 token 不算受理
            body = getattr(pair.trace, "body", b"") or b""
            if pair.trace.is_normal() and b"access_token" in body:
                accepted.append(label)

        assertion: dict[str, Any] = {
            "kind": "code_format",
            "expected": {"probe_status": [200, 400, 401, 403]},
            "observed": {
                "probes": len(probes),
                # 只写形状名：畸形码的"值"不进证据
                "accepted_shapes": sorted(accepted),
            },
            "fingerprint": {"format_unchecked": bool(accepted)},
        }
        if any(p.trace.error for p in probes):
            return self.inconclusive("fetch_failed", assertion)

        result = checks.judge_code_format(
            malformed_accepted=tuple(accepted), probe_count=len(probes)
        )
        assertion["judge"] = result.reason
        if result.verdict == "VERIFIED":
            return self.verified(result.reason, assertion, 0.9)
        if result.verdict == "REFUTED":
            return self.refuted(result.reason, assertion)
        return self.inconclusive(result.reason, assertion)


__all__ = ["CodeFormat", "MALFORMED", "VALID_CODE"]
