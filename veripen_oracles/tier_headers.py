"""响应头类 oracle（B123）——先做 Content-Security-Policy 缺失。

为什么从这条开始：判定完全来自**响应头**，不需要浏览器、不需要新传输、不需要载荷。
一次 GET + 一次对照 GET，就能给出"有没有 CSP"这个可复核的事实。

判据（缺一不判）：
1. 基线响应 2xx（`baseline_ok` 已保证）；
2. 基线**和**对照都没有 `Content-Security-Policy` 头——两独立请求都为空才算稳定缺失；
3. 任一出现该头 → 判否（有 CSP，只是强弱另说）。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from veripen_core.models import ImpactClass, Judgement

from .common.base import BaseOracle, OracleMeta, variant
from .common.harness import Pair, Variant

HEADER = "content-security-policy"


class HeaderParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _csp_of(trace: Any) -> str:
    """大小写不敏感地取 CSP；没有就是空串（HTTP 头名不区分大小写）。"""
    for name, value in (getattr(trace, "headers", {}) or {}).items():
        if str(name).lower() == HEADER:
            return str(value or "").strip()
    return ""


class CspAbsent(BaseOracle):
    """缺少 Content-Security-Policy。**只说"没有"，不说"弱"。**"""

    params_model = HeaderParams

    meta = OracleMeta(
        id="csp_absent",
        version="1.0.0",
        cwe="CWE-693",
        impact=ImpactClass.READ_ONLY,
        severity_rule="missing_security_header",
        selector_tags=("html_document",),
    )

    def baseline_params(self, binding: Any) -> dict[str, str]:
        # 头类判定不需要参数：基线就是"原样请求一次"
        return {}

    def baseline_ok(self, trace: Any) -> bool:
        return bool(trace.is_normal())

    def plan(self, binding: Any) -> list[Variant]:
        # probe：断言"它缺这个头"的那次请求（原样发）
        # control：同一条面再发一次（带无害参数），两次都缺才算稳定
        return [
            variant("csp_probe", "probe", expect="normal"),
            variant("csp_again", "control", expect="normal",
                    extra_params={"vp_csp": "1"}),
        ]

    def judge(self, pairs: list[Pair]) -> Judgement:
        by_role = {p.role: p for p in pairs}
        probe = by_role.get("probe")
        control = by_role.get("control")
        if probe is None or control is None:
            return self.inconclusive("missing_variant")
        probe_csp = _csp_of(probe.trace)
        ctrl_csp = _csp_of(control.trace)
        base_csp = probe_csp

        assertion = {
            "kind": "missing_security_header",
            "statement": "两次独立请求都没有 Content-Security-Policy 响应头",
            # expected 的 schema 是 dict[str, list[int]]：1 = 有 CSP / 0 = 没有
            "expected": {"has_csp": [1]},
            "observed": {
                "probe_has_csp": bool(probe_csp),
                "control_has_csp": bool(ctrl_csp),
                "probe_status": probe.trace.status,
                "control_status": control.trace.status,
            },
            "fingerprint": {"csp_absent": not probe_csp and not ctrl_csp},
        }

        if probe.trace.error or control.trace.error:
            return self.inconclusive("fetch_failed", assertion)
        if not probe_csp and not ctrl_csp:
            return self.verified("csp_header_absent", assertion, 0.9)
        return self.refuted("csp_header_present", assertion)


__all__ = ["HEADER", "CspAbsent"]
