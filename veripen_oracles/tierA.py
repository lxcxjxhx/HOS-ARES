"""4 个 TierA oracle（B5）+ 一个判定纯函数模块。

目录形状仍按 07 §6 的"一个 oracle 一个目录"，本文件是它们的**实现合并版**：
4 个 oracle 加起来不到 300 行，拆成 4 个包只会让 review 变慢。
B6/B7 起 oracle 数量上来后再按目录拆（那时每个都会长到 150+ 行）。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from veripen_core.models import ImpactClass, Judgement, Verdict

from .common.base import BaseOracle, OracleMeta, transform, variant
from .common.harness import Pair, Transform, Variant

# ══════════════════════════════════════════════════════════════════
# 1 sqli_bool_diff · CWE-89
# ══════════════════════════════════════════════════════════════════
SQLI_PARAMS: tuple[str, ...] = ()


class SqliBoolDiff(BaseOracle):
    """布尔差分：``1=1`` 与 ``1=2`` 的**响应差异**，加上同形态无害对照。

    精髓是**对照**：任何"参数一变页面就变"的站点都会假阳性，所以
    ``control_true`` / ``control_false`` 必须是同长度、同形态、无害的变换，
    且二者指纹必须相同。
    """

    meta = OracleMeta(
        id="sqli_bool_diff",
        version="1.0.0",
        cwe="CWE-89",
        impact=ImpactClass.READ_ONLY,
        severity_rule="differential_data_leak",
        selector_tags=("param_injectable",),
    )

    def baseline_params(self, binding: Any) -> dict[str, str]:
        first = (binding.param_names or ("q",))[0]
        return {first: "widget"}

    def plan(self, binding: Any) -> list[Variant]:
        return [
            # 真条件与基线同形：它必须是"没有差异"的那一侧，才证明差异来自条件而非参数长度
            variant("bool_true", "readback", expect="normal",
                    tf=transform("wrap_numeric_bool", " AND 1=1", note="真条件")),
            # 假条件才是差分探针：响应必须与基线不同，否则参数根本没进查询
            variant("bool_false", "probe", expect="differs",
                    tf=transform("wrap_numeric_bool", " AND 1=2", note="假条件")),
            variant("control_true", "control", expect="normal",
                    tf=transform("append", "x", note="无害同形态对照 x")),
            variant("control_false", "control", expect="normal",
                    tf=transform("append", "y", note="无害同形态对照 y")),
        ]

    def judge(self, pairs: list[Pair]) -> Judgement:
        by_name = {p.variant.name: p for p in pairs}
        required = ("bool_true", "bool_false", "control_true", "control_false")
        if not all(n in by_name for n in required):
            return self.inconclusive("missing_variant")
        t, f = by_name["bool_true"], by_name["bool_false"]
        ct, cf = by_name["control_true"], by_name["control_false"]

        if any(p.trace.status >= 500 for p in (ct, cf)):
            return self.inconclusive("control_5xx", {"control_status": [ct.trace.status, cf.trace.status]})

        diff = t.trace.fingerprint() != f.trace.fingerprint()
        control_same = ct.trace.fingerprint() == cf.trace.fingerprint()
        assertion = {
            "kind": "boolean_differential",
            "expected": {"probe_status": [200], "control_status": [200]},
            "fingerprint": {"bool_differs": diff, "control_equal": control_same},
            "observed": {
                "true": list(t.trace.fingerprint()),
                "false": list(f.trace.fingerprint()),
            },
        }
        if diff and control_same:
            return self.verified("boolean_differential_holds", assertion, 0.9)
        return self.refuted("no_differential" if not diff else "control_also_differs", assertion)


# ══════════════════════════════════════════════════════════════════
# 2 idor_cross_account · CWE-639
# ══════════════════════════════════════════════════════════════════
class IdorParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repeat: int = 2
    #: 归属受测账号的那条对象（含于 profile 的只读范围）
    subject_object_id: int = 1
    #: 一条"任何人都读不到"的对象，用来证明系统确实有对象级鉴权
    locked_object_id: int = 4


class IdorCrossAccount(BaseOracle):
    """对象级越权。control 是灵魂：证明系统确实有对象级鉴权，只是漏了。

    注意条件 B 与其他三类相反：probe 要**成功**（200），且 body 与基线**相同**。
    "整站不鉴权"不是 IDOR，所以必须靠 control 403 把那种情况排掉。
    """

    params_model = IdorParams

    meta = OracleMeta(
        id="idor_cross_account",
        version="1.0.0",
        cwe="CWE-639",
        impact=ImpactClass.READ_ONLY,
        severity_rule="cross_account_read",
        selector_tags=("object_read",),
        requires=("auth:secondary",),
    )

    def baseline_params(self, binding: Any) -> dict[str, str]:
        # 先证明"对象属于 primary"：取一条确实归 primary 的对象（由 profile 的扫描范围决定）
        first = (binding.param_names or ("id",))[0]
        # 基线必须由**拥有者**读（否则基线自己就 403），身份由变体声明
        return {first: str(getattr(self.params, "subject_object_id", 1))}

    def baseline_ok(self, trace: Any) -> bool:
        # 基线的 body 必须是"一个真实对象"，不只看 200
        return trace.is_normal() and b'"owner"' in trace.body

    def plan(self, binding: Any) -> list[Variant]:
        locked = str(getattr(self.params, "locked_object_id", 4))
        return [
            # probe：非拥有者读**同一个**对象。期望 200——这才是"漏了对象级"，
            # 而不是"整站没鉴权"（那种情况由 control 排掉）。
            variant("stranger_reads_subject", "probe", expect="normal",
                    tf=None, account="STRANGER"),  # id 不变，只换账号
            # control：非拥有者读一条**任何非管理员都读不到**的对象，期望 403。
            variant("stranger_reads_locked", "control", expect="deny",
                    tf=transform("replace", locked, note="非拥有者读锁定对象"),
                    account="STRANGER"),
        ]

    def judge(self, pairs: list[Pair]) -> Judgement:
        by_name = {p.variant.name: p for p in pairs}
        probe = by_name.get("stranger_reads_subject")
        control = by_name.get("stranger_reads_locked")
        if probe is None or control is None:
            return self.inconclusive("missing_variant")

        same_body = probe.trace.body == probe.baseline.body
        probe_ok = probe.trace.is_normal()
        control_denied = control.trace.status in (401, 403)
        assertion = {
            "kind": "cross_account",
            "statement": "secondary 读取 primary 的对象成功，且访问无权对象被拒",
            "expected": {"probe_status": [200, 204], "control_status": [401, 403]},
            "observed": {"probe_status": probe.trace.status, "control_status": control.trace.status},
            "fingerprint": {"probe_body_eq_baseline": same_body, "control_denied": control_denied},
        }
        if probe_ok and same_body and control_denied:
            return self.verified("object_level_authz_missing", assertion, 0.95)
        if not control_denied:
            return self.refuted("control_not_denied", assertion)
        return self.refuted("probe_not_reproduced", assertion)


# ══════════════════════════════════════════════════════════════════
# 3 ssrf_oob · CWE-918
# ══════════════════════════════════════════════════════════════════
class SsrfOob(BaseOracle):
    """外带证明。判定只看"服务端是否真的连了 OOB"，不看响应体。

    ``OobProbe`` 在 run 前后做快照差（靶场的 callbacks / 真 listener 的记录），
    框架据此在 trace 的 header 上打 ``oob_seen``；judge 只读 trace，保持纯函数。
    """

    meta = OracleMeta(
        id="ssrf_oob",
        version="1.0.0",
        cwe="CWE-918",
        impact=ImpactClass.READ_ONLY,
        severity_rule="server_side_fetch",
        selector_tags=("url_fetch",),
        requires=("oob:listener",),
    )

    def baseline_params(self, binding: Any) -> dict[str, str]:
        first = (binding.param_names or ("url",))[0]
        return {first: "doc-SAMPLE"}

    def plan(self, binding: Any) -> list[Variant]:
        return [
            # 目标值一律走符号：OOB 接入点与"不该被连的主机"都由部署注入，
            # 靶场把这两个符号解析成它自己的假域；生产解析成 listener 与黑名单域。
            variant("oob_probe", "probe", expect="callback",
                    tf=transform("replace", "{{ref:OOB}}/{{nonce}}", note="指向 OOB 通道")),
            variant("local_control", "control", expect="no_callback",
                    tf=transform("replace", "{{ref:DEAD_HOST}}/{{nonce}}",
                                 note="指向不存在的主机，不应有回调")),
        ]

    def judge(self, pairs: list[Pair]) -> Judgement:
        by_name = {p.variant.name: p for p in pairs}
        probe, control = by_name.get("oob_probe"), by_name.get("local_control")
        if probe is None or control is None:
            return self.inconclusive("missing_variant")
        seen = probe.trace.headers.get("oob_seen") == "1"
        control_seen = control.trace.headers.get("oob_seen") == "1"
        assertion = {
            "kind": "oob_callback",
            "expected": {"probe_status": [200, 500], "control_status": [200, 400, 500]},
            "observed": {"oob_seen": bool(seen), "control_seen": bool(control_seen)},
            "fingerprint": {"oob_seen": bool(seen), "control_no_callback": not control_seen},
            "nonce_present": seen,
        }
        if seen and not control_seen:
            return self.verified("server_fetched_oob", assertion, 0.9)
        if seen and control_seen:
            return self.inconclusive("any_url_is_fetched", assertion)
        return self.refuted("no_callback", assertion)


# ══════════════════════════════════════════════════════════════════
# 4 jwt_replay · CWE-347
# ══════════════════════════════════════════════════════════════════
class JwtParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repeat: int = 2
    #: 用来"证明服务器确实校验签名"的原始 token：由 profile 的 secret_ref 注入。
    #: 缺它 → plan 返回空 → 骨架给 INCONCLUSIVE(no_control)。**绝不在 oracle 里造 token**。
    subject: str = "secondary"
    escalate_to: str = "primary"
    escalate_role: str = "admin"


class JwtReplay(BaseOracle):
    """JWT 校验缺陷。control 证明"服务器确实校验签名"，否则"乱 token 都 200"被误判成漏洞。

    oracle 的域知识是 **token 结构**（header.payload.signature），不是密钥：
    它把用户给的 token 拆开、改 header/payload、再按原样拼回去。
    如何拿到"一个真实 token"属于 ``token_issue`` 绑定（tokens 由 profile 的 secret_ref 提供）。
    """

    params_model = JwtParams
    baseline_uses_token = True

    meta = OracleMeta(
        id="jwt_replay",
        version="1.0.0",
        cwe="CWE-347",
        impact=ImpactClass.READ_ONLY,
        severity_rule="token_acceptance",
        selector_tags=("protected_read",),
        requires=("auth:primary", "token:real"),
    )

    def baseline_params(self, binding: Any) -> dict[str, str]:
        return {}  # 真实 token 走 header（由 run 的 token 注入），不进 params

    def baseline_ok(self, trace: Any) -> bool:
        return trace.is_normal() and b'"sub"' in trace.body

    def plan(self, binding: Any) -> list[Variant]:
        p = self.params
        subject = getattr(p, "subject", "secondary")
        escalate_to = getattr(p, "escalate_to", "primary")
        escalate_role = getattr(p, "escalate_role", "admin")

        def forge(header_mut: dict[str, Any] | None, payload_mut: dict[str, Any] | None, keep_sig: bool) -> str:
            return "{{jwt:" + _jwt_spec(header_mut, payload_mut, keep_sig) + "}}"

        return [
            variant("alg_none", "probe", expect="normal",
                    tf=variant_transform("none"),
                    headers={"Authorization": "Bearer " + forge({"alg": "none"}, {}, False)}),
            # 改 payload 但不改签名：签名校验正常时**必然**被拒，这正是 REPLAY 的常见形态
            variant("claim_tamper", "probe", expect="deny",
                    tf=variant_transform("claim"),
                    headers={"Authorization": "Bearer " + forge(
                        None, {"sub": escalate_to, "role": escalate_role}, True)}),
            variant("invalid_signature", "control", expect="deny",
                    tf=variant_transform("bad"),
                    headers={"Authorization": "Bearer " + forge(None, None, "break")}),  # type: ignore[arg-type]
        ]

    def judge(self, pairs: list[Pair]) -> Judgement:
        by_name = {p.variant.name: p for p in pairs}
        none_p, tamper_p = by_name.get("alg_none"), by_name.get("claim_tamper")
        control = by_name.get("invalid_signature")
        if none_p is None or tamper_p is None or control is None:
            return self.inconclusive("missing_variant")

        control_denied = control.trace.status in (401, 403)
        none_ok = none_p.trace.is_normal()
        escalate_role = getattr(self.params, "escalate_role", "admin")
        tamper_ok = tamper_p.trace.is_normal() and (
            b'"role": "' + escalate_role.encode() + b'"' in tamper_p.trace.body
        )
        assertion = {
            "kind": "jwt_acceptance",
            "statement": "无效签名被拒（服务器确实校验），但 alg=none 或声明篡改被接受",
            "expected": {"probe_status": [200], "control_status": [401, 403]},
            "observed": {
                "alg_none": none_p.trace.status,
                "claim_tamper": tamper_p.trace.status,
                "invalid_signature": control.trace.status,
            },
            "fingerprint": {
                "control_denied": control_denied,
                "alg_none_accepted": none_ok,
                "claim_tampered_accepted": tamper_ok,
            },
        }
        if control_denied and (none_ok or tamper_ok):
            return self.verified("token_validation_defect", assertion, 0.9)
        if not control_denied:
            return self.refuted("control_not_denied", assertion)
        return self.refuted("variants_rejected", assertion)


def _jwt_spec(header_mut: dict[str, Any] | None, payload_mut: dict[str, Any] | None, keep_sig: Any) -> str:
    """把 JWT 变体压成可内联解析的规格串（值里不含冒号，避免解析歧义）。"""
    import base64
    import json as _json

    def enc(obj: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(_json.dumps(obj, separators=(",", ":")).encode()).rstrip(b"=").decode()

    return "|".join([enc(header_mut or {}), enc(payload_mut or {}), str(keep_sig)])


def jwt_from_template(token: str, spec: str) -> str:
    """真实值在此生成：拆原始 token → 打补丁 → 按 keep_sig 决定是否保留签名。

    ``keep_sig="break"`` 时把签名最后两位改成非 base64url 字符，制造"无效签名"，
    且**不复用任何密钥**——control 的意义就是"服务器拒绝这段签名"。
    """
    import base64
    import json as _json

    parts = token.split(".")
    if len(parts) != 3:
        return token
    head_b, head_m, keep = spec.split("|")
    head_patch = _json.loads(base64.urlsafe_b64decode(head_b + "=" * (-len(head_b) % 4))) if head_b else {}
    payload_patch = _json.loads(base64.urlsafe_b64decode(head_m + "=" * (-len(head_m) % 4))) if head_m else {}
    header = {**_json.loads(base64.urlsafe_b64decode(parts[0] + "=" * (-len(parts[0]) % 4))), **head_patch}
    payload = {**_json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))), **payload_patch}
    def enc(obj: dict[str, Any]) -> str:
        raw = _json.dumps(obj, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    sig = parts[2]
    if keep == "break":
        sig = (sig[:-2] if len(sig) > 2 else sig) + ("AA" if not sig.endswith("AA") else "BB")
    if keep == "False":
        sig = ""
    return f"{enc(header)}.{enc(payload)}.{sig}"


def variant_transform(kind: str) -> Transform:
    """JWT 类的变体靠 header 携带真实值；transform 只做占位，保持 Variant 结构统一。"""
    return transform("replace", arg=kind, target="header", note=f"jwt:{kind}")


# ══════════════════════════════════════════════════════════════════
# 注册表
# ══════════════════════════════════════════════════════════════════
REGISTRY: tuple[type[BaseOracle], ...] = (
    SqliBoolDiff,
    IdorCrossAccount,
    SsrfOob,
    JwtReplay,
)

def by_id(oracle_id: str, **params: Any) -> BaseOracle:
    for cls in REGISTRY:
        if cls.meta.id == oracle_id:
            model = cls.params_model
            return cls(params=model(**params)) if model is not None else cls()
    raise KeyError(oracle_id)


__all__ = [
    "IdorCrossAccount",
    "IdorParams",
    "JwtParams",
    "JwtReplay",
    "REGISTRY",
    "SqliBoolDiff",
    "SsrfOob",
    "by_id",
]
