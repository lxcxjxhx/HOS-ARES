"""响应头类 oracle（B123 起）：只看响应头，不需要载荷 / 浏览器 / 新传输。

现有的三条：

| oracle | 缺什么 | CWE | 备注 |
|---|---|---|---|
| `csp_absent` | `Content-Security-Policy` | 693 | B123 |
| `frame_opt_absent` | `X-Frame-Options` 且 CSP 里也没有 `frame-ancestors` | 1021 | B124 |
| `nosniff_absent` | `X-Content-Type-Options` 不是 `nosniff` | 693 | B124 |

共同判据（缺一不判）：
1. 两次请求都是 2xx（`baseline_ok` 保证基线，control 自己也看）；
2. **probe 与 control 都"没有"** —— 两独立请求都缺才算稳定缺失；
3. 任一次"有" → 判否（有就是有，强弱另说）。

**没做的**：HSTS。它只对 https 有效，而 binding 的 scheme 现在传不到 judge，
硬做会把 http 目标判成一堆假缺失——宁可留着，等 scheme 能进 Trace 再说。
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from veripen_core.models import ImpactClass, Judgement

from .common.base import BaseOracle, OracleMeta, variant
from .common.harness import Pair, Variant

CSP = "content-security-policy"
XFO = "x-frame-options"
NOSNIFF = "x-content-type-options"


class HeaderParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _header_of(trace: Any, name: str) -> str:
    """大小写不敏感地取一个响应头；没有就是空串（HTTP 头名不区分大小写）。"""
    for key, value in (getattr(trace, "headers", {}) or {}).items():
        if str(key).lower() == name:
            return str(value or "").strip()
    return ""


class _HeaderAbsent(BaseOracle):
    """响应头缺失类判定的共同骨架：子类只声明"看哪个头、缺了叫什么"。"""

    params_model = HeaderParams
    #: 判定依据的头名（小写）
    header_name: ClassVar[str] = ""
    #: 判定成立时的 reason
    absent_reason: ClassVar[str] = "header_absent"
    #: 判定不成立时的 reason
    present_reason: ClassVar[str] = "header_present"

    def baseline_params(self, binding: Any) -> dict[str, str]:
        # 头类判定不需要参数：基线就是"原样请求一次"
        return {}

    def baseline_ok(self, trace: Any) -> bool:
        return bool(trace.is_normal())

    def plan(self, binding: Any) -> list[Variant]:
        # probe：断言"它缺这个头"的那次请求；control：同一条面再发一次（带无害参数）
        return [
            variant("hdr_probe", "probe", expect="normal"),
            variant("hdr_again", "control", expect="normal",
                    extra_params={"vp_hdr": "1"}),
        ]

    def _present(self, trace: Any) -> bool:
        """这条响应算不算"有保护"。子类可以覆盖（例：CSP frame-ancestors 也算）。"""
        return bool(_header_of(trace, self.header_name))

    def judge(self, pairs: list[Pair]) -> Judgement:
        by_role = {p.role: p for p in pairs}
        probe = by_role.get("probe")
        control = by_role.get("control")
        if probe is None or control is None:
            return self.inconclusive("missing_variant")

        probe_has = self._present(probe.trace)
        ctrl_has = self._present(control.trace)
        assertion = {
            "kind": "missing_security_header",
            "statement": f"两次独立请求都没有提供 {self.header_name} 保护",
            # expected 的 schema 是 dict[str, list[int]]：1 = 有保护 / 0 = 没有
            "expected": {"has_protection": [1]},
            "observed": {
                "probe_has": probe_has,
                "control_has": ctrl_has,
                "probe_status": probe.trace.status,
                "control_status": control.trace.status,
                "header": self.header_name,
            },
            "fingerprint": {"absent": not probe_has and not ctrl_has},
        }
        if probe.trace.error or control.trace.error:
            return self.inconclusive("fetch_failed", assertion)
        if not probe_has and not ctrl_has:
            return self.verified(self.absent_reason, assertion, 0.9)
        return self.refuted(self.present_reason, assertion)


class CspAbsent(_HeaderAbsent):
    """缺少 Content-Security-Policy。**只说"没有"，不说"弱"。**"""

    header_name = CSP
    absent_reason = "csp_header_absent"
    present_reason = "csp_header_present"

    meta = OracleMeta(
        id="csp_absent",
        version="1.0.0",
        cwe="CWE-693",
        impact=ImpactClass.READ_ONLY,
        severity_rule="missing_security_header",
        selector_tags=("html_document",),
    )


class FrameOptAbsent(_HeaderAbsent):
    """点击劫持防护缺失：既没有 X-Frame-Options，CSP 里也没有 frame-ancestors。"""

    header_name = XFO
    absent_reason = "frame_protection_absent"
    present_reason = "frame_protection_present"

    meta = OracleMeta(
        id="frame_opt_absent",
        version="1.0.0",
        cwe="CWE-1021",
        impact=ImpactClass.READ_ONLY,
        severity_rule="missing_security_header",
        selector_tags=("html_document",),
    )

    def _present(self, trace: Any) -> bool:
        """现代站点常用 CSP 的 frame-ancestors 代替 XFO——两者有其一就不算缺失。"""
        if _header_of(trace, XFO):
            return True
        return "frame-ancestors" in _header_of(trace, CSP).lower()


class NosniffAbsent(_HeaderAbsent):
    """缺少 `X-Content-Type-Options: nosniff`（浏览器会去猜类型）。"""

    header_name = NOSNIFF
    absent_reason = "nosniff_absent"
    present_reason = "nosniff_present"

    meta = OracleMeta(
        id="nosniff_absent",
        version="1.0.0",
        cwe="CWE-693",
        impact=ImpactClass.READ_ONLY,
        severity_rule="missing_security_header",
        selector_tags=("html_document",),
    )

    def _present(self, trace: Any) -> bool:
        # 只有写成 nosniff 才算：`X-Content-Type-Options: none` 等于没配
        return _header_of(trace, NOSNIFF).lower() == "nosniff"


__all__ = [
    "CSP",
    "NOSNIFF",
    "XFO",
    "CspAbsent",
    "FrameOptAbsent",
    "NosniffAbsent",
    "HeaderParams",
]
