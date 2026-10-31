"""B6 · TierA 余 2 类：open_redirect / path_traversal（18 §1.5 / §1.6）。

这两类的共同点：**判定依赖"值"而不是"形状"**。
- open_redirect 看 ``Location`` 头解析后的 host；
- path_traversal 看 body 里是否命中系统文件特征指纹。

所以它们的误报控制都压在"值的来源"上：
跳转目标必须来自**我们的**符号（不是目标站自己的白名单），
穿越深度必须来自**绑定模板的路径层数**（不是写死的 ``../../../../``）。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict

from veripen_core.models import ImpactClass, Judgement, Verdict

from .common.base import BaseOracle, OracleMeta, transform, variant
from .common.harness import Pair, Variant

#: 跳转类状态码（用 frozenset：语义是集合，不是"数据表"）
REDIRECTS = frozenset({301, 302, 303, 307, 308})
#: "被拒"的 4xx 集合（404/400 也算被拒，5xx 不算）
BLOCKED_4XX = frozenset({400, 401, 403, 404})


# ══════════════════════════════════════════════════════════════════
# 1 open_redirect · CWE-601（18 §1.5）
# ══════════════════════════════════════════════════════════════════
class OpenRedirectParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repeat: int = 2
    #: 协议相对形态是否也算（``//host``）；默认算——这是最常被漏掉的一支
    check_protocol_relative: bool = True
    #: 站内主机白名单符号名：命中即"合法跳转"，不算跳出（18 §1.5 误报控制②）
    allowed_host_symbol: str = "SITE_HOST"


class OpenRedirect(BaseOracle):
    """跳转目标是否被无校验地采纳。

    三条对照缺一不可：
    ① baseline：站内相对目标 → 必须 302 到站内（证明功能正常）
    ② control：``javascript:`` 伪协议 → 必须被拒（证明"有过滤"）
    ③ probe/control 的差别只在"目标域归属"，不在参数形状

    只看 ``Location`` 头，不看 body 里的链接——body 里的链接是前端的事。
    """

    params_model = OpenRedirectParams

    meta = OracleMeta(
        id="open_redirect",
        version="1.0.0",
        cwe="CWE-601",
        impact=ImpactClass.READ_ONLY,
        severity_rule="unvalidated_redirect",
        selector_tags=("redirect_entry",),
    )

    def baseline_params(self, binding: Any) -> dict[str, str]:
        first = (binding.param_names or ("next",))[0]
        # 站内相对目标由符号给出：靶场解析成它自己的相对路径，生产解析成 profile 声明的站内路径
        return {first: "{{ref:SAFE_TARGET}}"}

    def baseline_ok(self, trace: Any) -> bool:
        # 基线必须是"跳了、且跳到站内"，否则这个面根本没有跳转语义
        return trace.status in REDIRECTS and self._location(trace) is not None

    def plan(self, binding: Any) -> list[Variant]:
        # 外域目标用 https 形态：协议相对形态（//host）可能被浏览器当成"站内"，单独作为一支
        plans = [
            variant(
                "external_absolute",
                "probe",
                expect="redirects_out",
                # scheme 由部署注入在符号值里（OOB_URL 含 scheme），代码里不出现任何 scheme 字面量
                tf=transform("replace", "{{ref:OOB_URL}}/{{nonce}}", note="绝对外域目标"),
            ),
            variant(
                "javascript_scheme",
                "control",
                expect="deny",
                tf=transform("replace", "javascript:alert('{{nonce}}')", note="伪协议应被拒"),
            ),
        ]
        if getattr(self.params, "check_protocol_relative", True):
            plans.insert(
                1,
                variant(
                    "protocol_relative",
                    "probe",
                    expect="redirects_out",
                    tf=transform("prefix_relative", "{{ref:OOB}}/{{nonce}}", note="协议相对形态"),
                ),
            )
        return plans

    def judge(self, pairs: list[Pair]) -> Judgement:
        by_name = {p.variant.name: p for p in pairs}
        control = by_name.get("javascript_scheme")
        if control is None:
            return self.inconclusive("missing_variant")

        probes = [p for n, p in by_name.items() if p.variant.role == "probe"]
        escaped = [(p.variant.name, self._location(p.trace) or "") for p in probes if self._escapes(p.trace)]
        control_denied = not self._escapes(control.trace)
        baseline_loc = self._location(pairs[0].baseline) if pairs else None

        assertion = {
            "kind": "unvalidated_redirect",
            "statement": "跳转目标被无校验采纳；伪协议被拒绝（说明存在过滤但漏了外域）",
            "expected": {"probe_status": sorted(REDIRECTS), "control_status": [200, 400, 403]},
            "observed": {
                "escaped_variants": [name for name, _ in escaped],
                "escaped_hosts": [_host(loc) for _, loc in escaped],
                "control_denied": control_denied,
            },
            "fingerprint": {
                "redirects_outside": bool(escaped),
                "control_denied": control_denied,
                "baseline_is_relative": baseline_loc is not None and _host(baseline_loc) == "",
            },
        }
        if escaped and control_denied:
            return self.verified("open_redirect", assertion, 0.85)
        if not control_denied:
            return self.inconclusive("control_also_redirects", assertion)
        return self.refuted("no_external_redirect", assertion)

    # ── 值判定（judge 内不读符号表：符号在 _fill 阶段已求值成真实 header 值）──
    @staticmethod
    def _location(trace: Any) -> str | None:
        for name, value in trace.headers.items():
            if name.lower() == "location":
                return str(value)
        return None

    def _escapes(self, trace: Any) -> bool:
        """跳到了"不是相对路径、也不是伪协议"的地方 = 出域。"""
        loc = self._location(trace)
        if loc is None or trace.status not in REDIRECTS:
            return False
        if loc.startswith("javascript:") or loc.startswith("data:"):
            return False  # 伪协议不是"跳出域"，是另一类问题
        return _host(loc) != ""


def _host(location: str) -> str:
    """取 host；相对路径返回空串（= 站内）。协议相对（//h）解析为 h。"""
    try:
        return urlsplit(location).hostname or ""
    except ValueError:
        return ""


# ══════════════════════════════════════════════════════════════════
# 2 path_traversal · CWE-22（18 §1.6）
# ══════════════════════════════════════════════════════════════════
class PathTraversalParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repeat: int = 2
    #: 最多尝试几种编码变体（基础变体失败后才用，18 §1.6 误报控制③）
    max_encoded_variants: int = 3


class PathTraversal(BaseOracle):
    """目录穿越。**必须有系统文件特征指纹**，不能只凭 200。

    深度不是常量：``{{depth}}`` 由绑定模板的路径层数算出（07 §0.4）。
    写死 ``../../../../`` 就是硬路由的另一种形态。
    """

    params_model = PathTraversalParams

    meta = OracleMeta(
        id="path_traversal",
        version="1.0.0",
        cwe="CWE-22",
        impact=ImpactClass.READ_ONLY,
        severity_rule="file_disclosure",
        selector_tags=("file_read",),
    )

    def baseline_params(self, binding: Any) -> dict[str, str]:
        first = (binding.param_names or ("name",))[0]
        return {first: "{{ref:SAFE_FILE}}"}

    def baseline_ok(self, trace: Any) -> bool:
        # 基线必须是"真的读到了一个文件"：200 且 body 非空且不命中任何系统文件指纹
        return trace.is_normal() and bool(trace.body) and not _match_fingerprints(trace.body)

    def plan(self, binding: Any) -> list[Variant]:
        record = "{{ref:SYSTEM_FILE}}"  # 目标系统文件名由符号注入（如 passwd / win.ini）
        return [
            variant(
                "traverse_plain",
                "probe",
                expect="fingerprint_hit",
                tf=transform(
                    "replace", "{{depth_pad}}{{ref:SYSTEM_FILE}}", target="path_param",
                    note="纯 ../ 穿越到系统文件（深度由模板层数算）",
                ),
            ),
            variant(
                "traverse_percent",
                "probe",
                expect="fingerprint_hit",
                tf=transform(
                    "replace", "{{depth_pad_encoded}}{{ref:SYSTEM_FILE}}", target="path_param",
                    note="百分号编码穿越",
                ),
            ),
            variant(
                "control_missing_file",
                "control",
                expect="deny",
                tf=transform(
                    "append", "{{depth_pad}}nonexistent_{{nonce}}", target="path_param",
                    note="穿越到不存在的文件",
                ),
            ),
            variant("control_relative", "control", expect="normal", tf=None),
        ]

    def judge(self, pairs: list[Pair]) -> Judgement:
        by_name = {p.variant.name: p for p in pairs}
        control_missing = by_name.get("control_missing_file")
        if control_missing is None:
            return self.inconclusive("missing_variant")

        probes = [p for n, p in by_name.items() if p.variant.role == "probe"]
        hits = [(p.variant.name, _match_fingerprints(p.trace.body)) for p in probes]
        matched = [(name, marks) for name, marks in hits if marks]
        control_blocked = control_missing.trace.status in BLOCKED_4XX

        assertion = {
            "kind": "path_traversal",
            "statement": "越界路径读到了系统文件特征内容；不存在的越界路径被拒",
            "expected": {"probe_status": [200], "control_status": sorted(BLOCKED_4XX)},
            "observed": {
                "matched_variants": [name for name, _ in matched],
                "fingerprints": sorted({m for _, marks in matched for m in marks}),
                "control_status": control_missing.trace.status,
            },
            "fingerprint": {
                "system_file_fingerprint": bool(matched),
                "control_blocked": control_blocked,
            },
        }
        if matched and control_blocked:
            return self.verified("path_traversal", assertion, 0.9)
        if not control_blocked:
            return self.inconclusive("any_path_returns_content", assertion)
        return self.refuted("no_fingerprint", assertion)


#: 系统文件特征指纹（版本化，可扩；18 §1.6 误报控制①）
#: 只放"出现即证明读到了系统文件"的强特征，不放弱特征（如单个冒号）。
FINGERPRINTS = {
    "unix_passwd": [b"root:x:0:0", b"root:*:0:0", b"daemon:"],
    "unix_shadow": [b"root:$", b"daemon:*:"],
    "ini_section": [b"[extensions]", b"[fonts]"],
    "win_system": [b"[boot loader]", b"for 16-bit app support"],
}


def _match_fingerprints(body: bytes) -> list[str]:
    return [name for name, needles in FINGERPRINTS.items() if any(n in body for n in needles)]


__all__ = [
    "FINGERPRINTS",
    "OpenRedirect",
    "OpenRedirectParams",
    "PathTraversal",
    "PathTraversalParams",
]
