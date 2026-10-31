"""B7 · TierB 5 类（18 §2）。

B 档与 A 档的差别不是"更难"，是**需要更多步骤或多角色**：

| oracle | 多出来的东西 |
|---|---|
| ``sqli_error_based`` | 指纹库（版本化、可解释） |
| ``bola_functional`` | 两条绑定（管理面 vs 普通面）——功能级越权 |
| ``xss_reflected_ctx`` | 三份载荷模板，字节级判"未转义" |
| ``mass_assignment`` | **写 + 回读**（唯一写操作，默认关闭） |
| ``rate_limit_absent`` | 并发与时间（默认关闭） |

两条纪律写进代码而不是文档：
1. **写操作与并发类默认 ``enabled: false``**，`policy.check_enabled` 在启动时校验；
2. 判定仍然只读 trace，不看时钟——`rate_limit` 的采样由框架做，oracle 只算 (S1,S2,R2)。
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict

from veripen_core.models import ImpactClass, Judgement, Verdict

from .common.base import BaseOracle, OracleMeta, transform, variant
from .common.harness import Pair, Variant

# ══════════════════════════════════════════════════════════════════
# 1 sqli_error_based · CWE-89（18 §2.1）
# ══════════════════════════════════════════════════════════════════
#: 数据库错误指纹库（版本化；每条都要"可解释"，报告里写命中的是哪一条）
ERROR_FINGERPRINTS: dict[str, tuple[bytes, ...]] = {
    "mysql": (b"SQL syntax", b"check the manual that corresponds to", b"Unclosed quotation mark"),
    "postgres": (b"pg_query", b"SQLSTATE", b"unterminated quoted string"),
    "oracle": (b"ORA-", b"ORA-01756", b"quoted string not properly terminated"),
    "mssql": (b"Incorrect syntax near", b"Unclosed quotation mark after"),
    "sqlite": (b"SQLITE_ERROR", b"unrecognized token"),
}
FINGERPRINT_VERSION = "2026.11.1"


class SqliErrorParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repeat: int = 2


class SqliErrorBased(BaseOracle):
    """报错注入：看**错误指纹**是否被触发，而不是看页面差多少。

    误报控制的重头在"排除全站通用报错模板"：同一指纹若在 control 也出现，
    说明那是框架的错误页，不是注入触发的。
    """

    params_model = SqliErrorParams

    meta = OracleMeta(
        id="sqli_error_based",
        version="1.0.0",
        cwe="CWE-89",
        impact=ImpactClass.READ_ONLY,
        severity_rule="error_based_injection",
        selector_tags=("param_injectable",),
    )

    def baseline_params(self, binding: Any) -> dict[str, str]:
        first = (binding.param_names or ("q",))[0]
        return {first: "widget"}

    def baseline_ok(self, trace: Any) -> bool:
        # 基线必须"干净"：一旦基线自己就带数据库错误指纹，这个面没法判
        return trace.is_normal() and not _match_errors(trace.body)

    def plan(self, binding: Any) -> list[Variant]:
        return [
            variant("quote_single", "probe", expect="any_response",
                    tf=transform("wrap_quote", "'", note="单引号闭合")),
            variant("quote_double", "probe", expect="any_response",
                    tf=transform("wrap_quote", '"', note="双引号闭合")),
            variant("quote_paren", "probe", expect="any_response",
                    tf=transform("append", "')", note="括号+引号闭合")),
            variant("control_illegal", "control", expect="normal",
                    tf=transform("append", "_", note="非法但不触发 SQL 解析")),
        ]

    def judge(self, pairs: list[Pair]) -> Judgement:
        by_name = {p.variant.name: p for p in pairs}
        control = by_name.get("control_illegal")
        if control is None:
            return self.inconclusive("missing_variant")

        probes = [p for p in pairs if p.variant.role == "probe"]
        hits = {p.variant.name: _match_errors(p.trace.body) for p in probes}
        matched = {name: marks for name, marks in hits.items() if marks}
        control_marks = _match_errors(control.trace.body)

        assertion = {
            "kind": "error_based_injection",
            "statement": "注入载荷触发了数据库错误指纹，对照与基线都没有",
            "expected": {"probe_status": [200, 500], "control_status": [200]},
            "observed": {
                "matched_variants": sorted(matched),
                "fingerprints": sorted({m for marks in matched.values() for m in marks}),
                "control_fingerprints": control_marks,
            },
            "fingerprint": {
                "error_fingerprint": bool(matched),
                "control_clean": not control_marks,
            },
            "fingerprint_version": FINGERPRINT_VERSION,
        }
        if matched and not control_marks:
            return self.verified("error_fingerprint_triggered", assertion, 0.85)
        if control_marks:
            # 全站通用报错模板：同一指纹在对照也出现 → 不是注入证据
            return self.refuted("generic_error_page", assertion)
        return self.refuted("no_error_fingerprint", assertion)


def _match_errors(body: bytes) -> list[str]:
    return [name for name, needles in ERROR_FINGERPRINTS.items() if any(n in body for n in needles)]


# ══════════════════════════════════════════════════════════════════
# 2 bola_functional · CWE-862（18 §2.2）
# ══════════════════════════════════════════════════════════════════
class BolaParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repeat: int = 2


class BolaFunctional(BaseOracle):
    """功能级越权：**同一功能，低权账号能不能用**。

    与 IDOR 的区别：IDOR 是"同接口不同对象"，这里是"同对象不同功能"。
    三条：
    ① baseline：高权打管理面 → 200
    ② probe：低权打**同一管理面** → 200（就是漏洞）
    ③ control：低权打普通面 → 200（证明这个账号本身可用）
    """

    params_model = BolaParams

    meta = OracleMeta(
        id="bola_functional",
        version="1.0.0",
        cwe="CWE-862",
        impact=ImpactClass.READ_ONLY,
        severity_rule="functional_authz_missing",
        selector_tags=("admin_function", "normal_function"),
        requires=("auth:secondary", "auth:primary"),
    )

    def baseline_params(self, binding: Any) -> dict[str, str]:
        return {}

    def baseline_ok(self, trace: Any) -> bool:
        # 管理面在高权下必须是 200 且带管理标记，否则这个面没有"管理语义"
        return trace.is_normal() and b'"admin"' in trace.body

    def plan(self, binding: Any) -> list[Variant]:
        return [
            variant("low_priv_admin", "probe", expect="normal",
                    account="STRANGER", binding_tag="admin_function"),
            variant("low_priv_normal", "control", expect="normal",
                    account="STRANGER", binding_tag="normal_function"),
        ]

    def judge(self, pairs: list[Pair]) -> Judgement:
        by_name = {p.variant.name: p for p in pairs}
        probe, control = by_name.get("low_priv_admin"), by_name.get("low_priv_normal")
        if probe is None or control is None:
            return self.inconclusive("missing_variant")

        probe_ok = probe.trace.is_normal() and b'"admin"' in probe.trace.body
        control_ok = control.trace.is_normal()
        assertion = {
            "kind": "functional_authz",
            "statement": "低权账号调用了管理功能并拿到管理数据；同账号的普通功能正常可用",
            "expected": {"probe_status": [200], "control_status": [200]},
            "observed": {"probe_status": probe.trace.status, "control_status": control.trace.status},
            "fingerprint": {"probe_admin_data": probe_ok, "control_account_usable": control_ok},
        }
        if probe_ok and control_ok:
            return self.verified("functional_authz_missing", assertion, 0.9)
        if not control_ok:
            return self.inconclusive("account_unusable", assertion)
        return self.refuted("admin_function_denied", assertion)


# ══════════════════════════════════════════════════════════════════
# 3 xss_reflected_ctx · CWE-79（18 §2.3）
# ══════════════════════════════════════════════════════════════════
class XssParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repeat: int = 2


class XssReflectedCtx(BaseOracle):
    """反射型 XSS：判**字节级未转义出现**，不执行 JS、不起浏览器（那是 C 档）。

    三份模板只是 `replace` 的三种 arg——上下文不同，kind 相同（07 §0.4）。
    每份独立判定：报告要写清"哪个上下文、哪个闭合字符"。
    """

    params_model = XssParams

    meta = OracleMeta(
        id="xss_reflected_ctx",
        version="1.0.0",
        cwe="CWE-79",
        impact=ImpactClass.READ_ONLY,
        severity_rule="reflected_unescaped",
        selector_tags=("echo_param",),
    )

    def baseline_params(self, binding: Any) -> dict[str, str]:
        first = (binding.param_names or ("q",))[0]
        return {first: "hello"}

    def plan(self, binding: Any) -> list[Variant]:
        return [
            variant("html_body", "probe", expect="normal",
                    tf=transform("replace", "<svg onload=alert('{{nonce}}')>",
                                 note="HTML 体上下文")),
            variant("attr_context", "probe", expect="normal",
                    tf=transform("replace", chr(34) + " onmouseover=" + chr(34)
                                 + "alert('{{nonce}}')" + chr(34), note="属性内上下文")),
            variant("js_string", "probe", expect="normal",
                    tf=transform("replace", "';alert('{{nonce}}')//", note="JS 字符串上下文")),
            variant("control_plain", "control", expect="normal",
                    tf=transform("replace", "plain-{{nonce}}", note="纯文本对照，应原样回显")),
        ]

    def judge(self, pairs: list[Pair]) -> Judgement:
        by_name = {p.variant.name: p for p in pairs}
        control = by_name.get("control_plain")
        if control is None:
            return self.inconclusive("missing_variant")

        rendered: dict[str, list[str]] = {}
        for p in pairs:
            if p.variant.role != "probe":
                continue
            payload = _payload_of(p)
            if payload and payload.encode() in p.trace.body:
                rendered[p.variant.name] = _context_offsets(p.trace.body, payload.encode())

        assertion = {
            "kind": "reflected_xss",
            "statement": "载荷以未转义原样出现在响应中（按上下文分别判定）",
            "expected": {"probe_status": [200], "control_status": [200]},
            "observed": {
                "unescaped_contexts": sorted(rendered),
                "offsets": {k: v for k, v in rendered.items()},
                "control_echoes": control.trace.body.count(b"plain-") > 0,
            },
            "fingerprint": {"unescaped_in_any_context": bool(rendered)},
        }
        if rendered:
            return self.verified("reflected_unescaped", assertion, 0.8)
        return self.refuted("escaped_or_absent", assertion)


def _write_field(binding: Any) -> str:
    names = tuple(getattr(binding, "param_names", ()) or ())
    return names[0] if names else "change"


def _payload_of(pair: Pair) -> str:
    """用**真正发出去**的参数值（nonce 已求值）；没有记录时回落到模板。"""
    for key, value in pair.sent.items():
        if key.startswith("__"):
            continue
        if any(mark in value for mark in ("<svg", "onmouseover", "alert(")):
            return str(value)
    return ""


def _context_offsets(body: bytes, needle: bytes) -> list[str]:
    """命中位置的字节偏移（证据要写清"在哪一行、命中哪一段"）。"""
    out: list[str] = []
    start = 0
    while len(out) < 3:
        idx = body.find(needle, start)
        if idx < 0:
            break
        out.append(f"@{idx}")
        start = idx + 1
    return out


# ══════════════════════════════════════════════════════════════════
# 4 mass_assignment · CWE-915（18 §2.4）—— 唯一写操作，默认关闭
# ══════════════════════════════════════════════════════════════════
class MassAssignParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repeat: int = 1  # 写操作：不重复（重复会叠加副作用）
    #: 只注入 profile 声明的只读字段（18 §2.4 误报控制②）
    readonly_field: str = "role"
    readonly_value: str = "admin"


class MassAssignment(BaseOracle):
    """批量赋值：**必须回读验证**。只看写入 200 会把"框架忽略未知字段"当成漏洞。

    impact = reversible_write：要过审批门，且动作会改状态 → repeat 固定为 1，
    并且把"回滚到原值"写进 assertion（B10 接审批与回滚执行）。
    """

    params_model = MassAssignParams

    meta = OracleMeta(
        id="mass_assignment",
        version="1.0.0",
        cwe="CWE-915",
        impact=ImpactClass.REVERSIBLE_WRITE,
        severity_rule="readonly_field_accepted",
        selector_tags=("profile_write", "profile_read"),
        requires=("approval:reversible_write",),
    )

    def baseline_params(self, binding: Any) -> dict[str, str]:
        # write 绑定声明了 change 字段，但基线阶段**还没写**：用 idempotent 值占位
        return {_write_field(binding): "baseline-{{nonce}}"}

    def baseline_ok(self, trace: Any) -> bool:
        return bool(trace.is_normal())

    def plan(self, binding: Any) -> list[Variant]:
        field = getattr(self.params, "readonly_field", "role")
        value = getattr(self.params, "readonly_value", "admin")
        return [
            # 注入口径：**多塞一个字段**（不是改已有字段的值）——只读字段被服务端接受才是漏洞
            variant("inject_readonly", "probe", expect="normal",
                    extra_params={field: value}, binding_tag="profile_write"),
            # probe = 写完之后回读；control = 写之前回读（框架保证 control 先发）
            variant("readback_after", "probe", expect="normal", binding_tag="profile_read"),
            variant("readback_before", "control", expect="normal", binding_tag="profile_read"),
        ]

    def judge(self, pairs: list[Pair]) -> Judgement:
        by_name = {p.variant.name: p for p in pairs}
        readback = by_name.get("readback_after")
        control = by_name.get("readback_before")
        if readback is None or control is None:
            return self.inconclusive("missing_variant")

        value = str(getattr(self.params, "readonly_value", "admin")).encode()
        field = str(getattr(self.params, "readonly_field", "role")).encode()
        needle = b'"' + field + b'": "' + value + b'"'
        accepted = needle in readback.trace.body
        control_clean = needle not in control.trace.body

        assertion = {
            "kind": "mass_assignment",
            "statement": "只读字段被写入并被回读为特权值；基线回读保持原值",
            "expected": {"probe_status": [200], "control_status": [200]},
            "observed": {
                "readback_accepted": bool(accepted),
                "baseline_field_value_unchanged": bool(control_clean),
            },
            "fingerprint": {"readonly_field_accepted": bool(accepted), "baseline_clean": bool(control_clean)},
            "rollback": {"field": field.decode(), "to": "original", "note": "B10 执行回滚并记入 capsule"},
        }
        if accepted and control_clean:
            return self.verified("readonly_field_accepted", assertion, 0.85)
        if not control_clean:
            return self.inconclusive("baseline_already_privileged", assertion)
        return self.refuted("field_ignored", assertion)


# ══════════════════════════════════════════════════════════════════
# 5 rate_limit_absent · CWE-770（18 §2.5）—— 并发类，默认关闭
# ══════════════════════════════════════════════════════════════════
class RateLimitParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repeat: int = 1
    #: 串行与并发的样本数（10 是 18 §2.5 的默认）
    samples: int = 10


class RateLimitAbsent(BaseOracle):
    """限流缺失：框架做采样（串行 S1 / 并发 S2 与 429 数 R2），oracle 只算结果。

    这条刻意**不套 baseline/probe 三条件**：它没有"对照组"，它比的是
    同一绑定在两种发送节奏下的成功率。所以 judge 读的是 ``BurstSamples``。
    报告分级是 info/low——这是"配置缺失"，不是"洞"（18 §2.5 误报控制③）。
    """

    params_model = RateLimitParams

    meta = OracleMeta(
        id="rate_limit_absent",
        version="1.0.0",
        cwe="CWE-770",
        impact=ImpactClass.READ_ONLY,
        severity_rule="config_missing_rate_limit",
        selector_tags=("rate_sensitive",),
    )

    #: 该 oracle 走 burst 采样，不走三条件骨架
    runner: str = "burst"

    def baseline_params(self, binding: Any) -> dict[str, str]:
        return {}

    def plan(self, binding: Any) -> list[Variant]:
        return []

    def judge(self, pairs: list[Pair]) -> Judgement:
        return self.inconclusive("use_judge_burst")

    def judge_burst(self, samples: Any) -> Judgement:
        n = int(getattr(self.params, "samples", 10))
        s1 = samples.serial_ok
        s2 = samples.concurrent_ok
        r2 = samples.concurrent_throttled
        assertion = {
            "kind": "rate_limit_absent",
            "statement": "串行与并发都全部成功，且没有任何 429/403 —— 该面没有限流",
            "expected": {"probe_status": [200], "control_status": [200]},
            "observed": {"serial_ok": s1, "concurrent_ok": s2, "throttled": r2, "samples": n},
            "fingerprint": {
                "no_throttling": r2 == 0,
                "all_succeeded": s2 == n and s1 == n,
            },
        }
        if s1 == n and s2 == n and r2 == 0:
            return Judgement(
                Verdict.VERIFIED, "no_rate_limiting", assertion, 0.7, "contract"
            )
        if r2 > 0:
            return self.refuted("throttling_observed", assertion)
        return self.inconclusive("partial_success", assertion)


__all__ = [
    "BolaFunctional",
    "BolaParams",
    "ERROR_FINGERPRINTS",
    "FINGERPRINT_VERSION",
    "MassAssignParams",
    "MassAssignment",
    "RateLimitAbsent",
    "RateLimitParams",
    "SqliErrorBased",
    "SqliErrorParams",
    "XssParams",
    "XssReflectedCtx",
]
