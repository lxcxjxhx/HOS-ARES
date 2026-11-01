"""C 档 3 类（B13）：存储型 XSS / CORS 配置 / GraphQL introspection。

这一档与前两档的差别不是「更难」，而是**需要跨会话或协议配合**，所以误报控制更依赖"证据形状"：

- ``xss_stored_probe``：必须**跨会话**读取（同会话可能是本地回显），且先确认写入成功；
- ``cors_misconfig``：只看响应头，不看 body；``*`` 且无 credentials 只算 low；
- ``graphql_introspection``：仅 introspection 可用 = info（很多产品故意开放），
  **必须叠加鉴权缺失**才算漏洞。
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict

from veripen_core.models import ImpactClass, Judgement, Verdict

from .common.base import BaseOracle, OracleMeta, transform, variant
from .common.harness import Pair, Variant

# ══════════════════════════════════════════════════════════════════
# 1 xss_stored_probe · CWE-79
# ══════════════════════════════════════════════════════════════════
class StoredXssParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repeat: int = 1  # 写操作：不重复（会叠加副作用）


class XssStoredProbe(BaseOracle):
    """存储型 XSS：写 → **换会话**读 → 判未转义。

    三条对照缺一不可：
    ① 写入后先用**写入者**读回，确认"内容真的存下来了"（不是被丢弃）；
    ② 读取必须**换账号**（同会话可能是本地回显，那不算存储）；
    ③ 读取响应里 nonce 必须以未转义原样出现。
    """

    params_model = StoredXssParams

    meta = OracleMeta(
        id="xss_stored_probe",
        version="1.0.0",
        cwe="CWE-79",
        impact=ImpactClass.REVERSIBLE_WRITE,
        severity_rule="stored_unescaped",
        selector_tags=("content_write", "content_read"),
        requires=("auth:primary", "auth:secondary"),
    )

    def baseline_params(self, binding: Any) -> dict[str, str]:
        return {"body": "baseline-{{nonce}}"}

    def baseline_ok(self, trace: Any) -> bool:
        return bool(trace.is_normal())

    def plan(self, binding: Any) -> list[Variant]:
        return [
            # 写入（reversible_write：会改状态，所以重复固定为 1）
            variant("write_payload", "probe", expect="any_response",
                    tf=transform("replace", "<svg onload=alert('{{nonce}}')>"),
                    binding_tag="content_write"),
            # 换账号读回：证明"存下来了且未转义"
            variant("read_as_other", "probe", expect="normal",
                    account="STRANGER", binding_tag="content_read"),
            # 对照：读回**别人写的普通内容**，证明读者本身能看到内容（排除空页面）
            variant("read_baseline", "control", expect="normal",
                    account="STRANGER", binding_tag="content_read"),
        ]

    def judge(self, pairs: list[Pair]) -> Judgement:
        by_name = {p.variant.name: p for p in pairs}
        write_p, read_p, control = (
            by_name.get("write_payload"),
            by_name.get("read_as_other"),
            by_name.get("read_baseline"),
        )
        if write_p is None or read_p is None or control is None:
            return self.inconclusive("missing_variant")

        write_ok = write_p.trace.status in (200, 201, 204)
        control_has_content = bool(control.trace.body.strip())
        nonce = _nonce_of(write_p)
        marker = _payload_marker(write_p)
        # 判据是"**载荷前缀原样出现**"，不是"nonce 出现"：
        # 实体化之后 nonce 往往还在（夹在 &#39; 之间），只看 nonce 会把 hardened 判成漏洞。
        raw_hit = bool(marker) and marker.encode() in read_p.trace.body
        escaped = (
            bool(nonce)
            and (b"&lt;svg" in read_p.trace.body or b"&#39;" in read_p.trace.body)
            and not raw_hit
        )

        assertion = {
            "kind": "stored_xss",
            "statement": "A 写入的载荷被 B 会话读到且未转义",
            "expected": {"probe_status": [200, 201], "control_status": [200]},
            "observed": {
                "write_status": write_p.trace.status,
                "read_status": read_p.trace.status,
                "raw_payload_present": raw_hit,
                "escaped_on_read": escaped,
                "control_has_content": control_has_content,
            },
            "fingerprint": {
                "write_accepted": write_ok,
                "cross_session_unescaped": raw_hit,
                "control_visible": control_has_content,
            },
            "rollback": {"marker": "nonce", "note": "任务结束按 nonce 删除（B10 审批门执行）"},
        }
        if not write_ok:
            return self.refuted("write_rejected", assertion)
        if not control_has_content:
            return self.inconclusive("control_empty_page", assertion)
        if raw_hit:
            return self.verified("stored_unescaped", assertion, 0.85)
        return self.refuted("escaped_or_stripped", assertion)

    @staticmethod
    def _sent_value(pair: Pair) -> str:
        for key, value in pair.sent.items():
            if not key.startswith("__") and "<svg" in value:
                return str(value)
        return ""


def _payload_marker(pair: Pair) -> str:
    """取载荷里"未转义才可能出现"的前缀（``<svg``）。"""
    value = XssStoredProbe._sent_value(pair)
    idx = value.find("<")
    return value[idx: idx + 8] if idx >= 0 else ""


def _nonce_of(pair: Pair) -> str:
    value = XssStoredProbe._sent_value(pair)
    for token in value.replace("(", " ").replace(")", " ").replace("'", " ").split():
        if token and not token.startswith("<") and token.lower() not in {"svg", "onload", "alert"}:
            return token
    return ""


# ══════════════════════════════════════════════════════════════════
# 2 cors_misconfig · CWE-942
# ══════════════════════════════════════════════════════════════════
class CorsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repeat: int = 2


class CorsMisconfig(BaseOracle):
    """CORS：**只看响应头**，不看 body（body 回显不是证据，18 §1.5 同理）。"""

    params_model = CorsParams

    meta = OracleMeta(
        id="cors_misconfig",
        version="1.0.0",
        cwe="CWE-942",
        impact=ImpactClass.READ_ONLY,
        severity_rule="acao_reflect",
        selector_tags=("api_read",),
    )

    def baseline_params(self, binding: Any) -> dict[str, str]:
        return {}

    def baseline_ok(self, trace: Any) -> bool:
        return bool(trace.is_normal())

    def plan(self, binding: Any) -> list[Variant]:
        return [
            variant("foreign_origin", "probe", expect="normal",
                    headers={"Origin": "{{ref:OOB_URL}}"}),
            variant("null_origin", "probe", expect="normal",
                    headers={"Origin": "null"}),
            # 对照：同源 Origin 必须被精确匹配（或干脆不回 ACAO）
            variant("same_origin", "control", expect="normal",
                    headers={"Origin": "{{ref:SITE_ORIGIN}}"}),
        ]

    def judge(self, pairs: list[Pair]) -> Judgement:
        by_name = {p.variant.name: p for p in pairs}
        control = by_name.get("same_origin")
        if control is None:
            return self.inconclusive("missing_variant")

        foreign = by_name.get("foreign_origin")
        null_p = by_name.get("null_origin")
        control_acao = _header(control.trace, "access-control-allow-origin")
        control_host = (control_acao or "").strip()
        reflected: list[str] = []
        credentials = False
        star = False
        for pair in (foreign, null_p):
            if pair is None:
                continue
            acao = _header(pair.trace, "access-control-allow-origin")
            acac = _header(pair.trace, "access-control-allow-credentials")
            if acao is None:
                continue
            sent_origin = _request_header(pair, "origin").strip()
            if acao == "*":
                star = True
                reflected.append("*")
            elif sent_origin and (
                acao.strip() == sent_origin or ("{{ref:" in sent_origin and acao.strip() != control_host)
            ):
                # 只认"回显了我们发出去的那个 Origin"；回显站内值不算反弹
                reflected.append(acao)
            if (acac or "").lower() == "true":
                credentials = True

        control_ok = control_acao is None or control_acao != "*"

        assertion = {
            "kind": "cors_misconfig",
            "statement": "外部 Origin 被回显到 Access-Control-Allow-Origin",
            "expected": {"probe_status": [200, 204], "control_status": [200, 204]},
            "observed": {
                "reflected": reflected,
                "allow_credentials": credentials,
                "control_acao": control_acao,
                "wildcard": star,
            },
            "fingerprint": {
                "origin_reflected": bool(reflected),
                "credentials_with_reflect": bool(reflected) and credentials,
            },
        }
        if not reflected:
            return self.refuted("origin_not_reflected", assertion)
        if star and not credentials:
            # ``*`` 且无 credentials → 传播面有限，只算 low（18 §3.2 误报控制②）
            return Judgement(Verdict.VERIFIED, "wildcard_origin_low", assertion, 0.6, "contract")
        if not control_ok:
            return self.inconclusive("control_also_wildcard", assertion)
        return self.verified("origin_reflected", assertion, 0.9 if credentials else 0.8)


def _request_header(pair: Pair, name: str) -> str:
    """变体发出的请求头（Variant.headers，nonce/符号已求值前的模板值也可读）。"""
    for key, value in pair.variant.headers.items():
        if key.lower() == name:
            return str(value)
    return ""


def _header(trace: Any, name: str) -> str | None:
    for key, value in trace.headers.items():
        if key.lower() == name:
            return str(value)
    return None


# ══════════════════════════════════════════════════════════════════
# 3 graphql_introspection · CWE-200
# ══════════════════════════════════════════════════════════════════
class GraphqlParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repeat: int = 2
    #: 低权下应当被拒的 mutation（由契约/观测给出，不在代码里猜）
    privileged_mutation: str = "adminAudit"


class GraphqlIntrospection(BaseOracle):
    """GraphQL：**仅 introspection 可用 = info**，必须叠加鉴权缺失才算漏洞。"""

    params_model = GraphqlParams

    meta = OracleMeta(
        id="graphql_introspection",
        version="1.0.0",
        cwe="CWE-200",
        impact=ImpactClass.READ_ONLY,
        severity_rule="introspection_or_authz",
        selector_tags=("graphql_endpoint",),
    )

    def baseline_params(self, binding: Any) -> dict[str, str]:
        return {}

    def baseline_ok(self, trace: Any) -> bool:
        # GraphQL 端点即使 400 也是"可用的"，只要有 JSON 响应体
        return trace.error is None and b"{" in trace.body

    def plan(self, binding: Any) -> list[Variant]:
        mutation = str(getattr(self.params, "privileged_mutation", "adminAudit"))
        return [
            variant("introspection", "probe", expect="normal",
                    headers={"x-vp-gql": json.dumps({"query": "{__schema{types{name}}}"})}),
            variant("privileged_mutation", "probe", expect="normal",
                    account="STRANGER",
                    headers={"x-vp-gql": json.dumps({"query": "mutation{" + mutation + "{ok}}"})}),
            variant("normal_query", "control", expect="normal",
                    headers={"x-vp-gql": json.dumps({"query": "{__typename}"})}),
        ]

    def judge(self, pairs: list[Pair]) -> Judgement:
        by_name = {p.variant.name: p for p in pairs}
        intro = by_name.get("introspection")
        mutation = by_name.get("privileged_mutation")
        control = by_name.get("normal_query")
        if intro is None or mutation is None or control is None:
            return self.inconclusive("missing_variant")

        schema_ok = b"__schema" in intro.trace.body or b"types" in intro.trace.body
        mutation_ok = mutation.trace.status in (200, 201) and b"error" not in mutation.trace.body
        control_ok = control.trace.status in (200, 201)

        assertion = {
            "kind": "graphql_introspection",
            "statement": "introspection 可用，且低权账号执行了受限 mutation",
            "expected": {"probe_status": [200], "control_status": [200]},
            "observed": {
                "introspection_available": schema_ok,
                "privileged_mutation_succeeded": mutation_ok,
                "control_ok": control_ok,
            },
            "fingerprint": {
                "introspection_available": schema_ok,
                "authz_missing": mutation_ok,
            },
        }
        if schema_ok and mutation_ok:
            return self.verified("introspection_and_authz_missing", assertion, 0.85)
        if schema_ok:
            # 仅 introspection 可用 = info（很多产品故意开放，18 §3.3 误报控制①）
            return Judgement(Verdict.VERIFIED, "introspection_only_info", assertion, 0.5, "contract")
        return self.refuted("introspection_disabled", assertion)


__all__ = [
    "CorsMisconfig",
    "CorsParams",
    "GraphqlIntrospection",
    "GraphqlParams",
    "StoredXssParams",
    "XssStoredProbe",
]
