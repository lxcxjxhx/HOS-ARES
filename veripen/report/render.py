"""报告三视图（B11）：**一份数据，三种投影**。

铁律（09 §1）：
- 全部从 ``findings + capsules`` 单向渲染，禁止三个格式各写一套逻辑；
- 渲染期断言：VERIFIED 无 capsule → 抛错；弱绑定 + high → 抛错；缺 route_ref → 抛错；
- 正文**零地址**：地址一律以 ``route_ref`` 呈现。

这里不放任何"客户名/路径/域名"字面量：牌面全来自入参。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

WEAK_STRENGTHS = frozenset({"crawl", "probe"})
HIGH_SEVERITIES = frozenset({"high", "critical"})
ROUTE_PREFIX_LEN = 8


class RenderError(RuntimeError):
    """渲染期断言失败：**不降级、不跳过**（09 §7 的强制方式）。"""

    code = "VP-6001"


class Outcome(str, Enum):
    """结论四态并列（09 §4 / 32 §3 A1）：禁止只报一个"已验证率"。"""

    REPORTED = "reported"       # 已验证并出报告
    UNDETERMINED = "undetermined"  # 跑了但判不了（INCONCLUSIVE）
    ERROR = "error"             # 没跑成（网络/靶标崩/工具异常）
    NOT_RUN = "not_run"         # 没跑（SKIPPED / 审批未过 / 预算未分配）


#: 措辞纪律（09 §4）：每态允许与禁止的词
WORDING: dict[str, dict[str, tuple[str, ...]]] = {
    "verified_strong": {"allow": ("存在", "可复现", "已验证"), "deny": ("可能", "疑似", "建议关注")},
    "verified_weak": {"allow": ("可能存在", "需人工复核"), "deny": ("确认", "高危")},
    "unverifiable": {"allow": ("无法确认", "需人工复核"), "deny": ("确认",)},
    "blocked": {"allow": ("按授权不可测试", "无该测试面"), "deny": ("未发现",)},
    "route_unresolved": {"allow": ("该测试面已不存在",), "deny": ("已修复",)},
    "route_drift": {"allow": ("该面结构已变更",), "deny": ("已修复", "未复现")},
}

TITLE_BY_STATUS = {
    "BLOCKED": "按授权不可测试",
    "ROUTE_UNRESOLVED": "该测试面已不存在",
    "ROUTE_DRIFT": "该面结构已变更",
    "INCONCLUSIVE": "未能判定",
    "ERROR": "执行失败",
    "NOT_RUN": "未执行",
}


@dataclass(frozen=True)
class FindingView:
    """渲染用的最小 finding 视图（来自库，不是模型自述）。"""

    finding_id: str
    title: str
    asset_id: str
    oracle_id: str
    cwe: str
    severity: str
    confidence: float
    route_strength: str
    route_ref: str | None
    capsule_id: str | None
    self_sha256: str | None
    replay_status: str = "NEVER"
    replay_at: int | None = None
    template_sha256: str = ""
    param_signature: str = ""
    sandbox_overridden: bool = False
    situation: str = ""          # 现象一句话（措辞受 lint 约束）
    impact: str = ""
    advice: str = ""
    outcome: Outcome = Outcome.REPORTED
    reason_code: str = ""        # undetermined / error / not_run 的原因码
    wording_key: str = ""        # 直接指定措辞档（默认按 state 推）

    @property
    def route_short(self) -> str:
        return (self.route_ref or "")[:ROUTE_PREFIX_LEN]

    @property
    def is_weak(self) -> bool:
        return self.route_strength in WEAK_STRENGTHS


@dataclass(frozen=True)
class ReportInput:
    engagement: str
    profile_hash: str
    image_digest: str
    root_sha256: str
    routeset_sha256: Sequence[str] = ()
    resolver_version: str = ""
    findings: Sequence[FindingView] = ()
    rules: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    coverage: Mapping[str, Any] = field(default_factory=dict)
    frameworks: Sequence[str] = ()
    distribution: str = "internal"
    render_time: str = ""
    #: 报告头部"结论四态"计数来源
    route_sources_failed: Sequence[str] = ()


# ══════════════════════════════════════════════════════════════════
# 断言（渲染期，缺一即抛）
# ══════════════════════════════════════════════════════════════════
def assert_renderable(report: ReportInput) -> None:
    """09 §7 的渲染期断言——**不降级、不跳过**。"""
    for f in report.findings:
        if f.outcome is Outcome.REPORTED:
            if not f.capsule_id:
                raise RenderError(f"{f.finding_id}: VERIFIED 无 capsule，拒绝渲染")
            if not f.self_sha256:
                raise RenderError(f"{f.finding_id}: capsule 缺 self_sha256")
            if not f.route_ref:
                raise RenderError(f"{f.finding_id}: 缺 route_ref（结论必须能指回面）")
        if f.is_weak and f.severity in HIGH_SEVERITIES:
            raise RenderError(
                f"{f.finding_id}: route_strength={f.route_strength} 不得标 {f.severity}"
                "（09 §3.1：弱绑定不给高严重度）"
            )
        if f.wording_key and not f.wording_key in WORDING:
            raise RenderError(f"{f.finding_id}: 未知措辞档 {f.wording_key}")
    if report.distribution not in {"internal", "partner", "customer"}:
        raise RenderError(f"未知 distribution: {report.distribution}")


# ══════════════════════════════════════════════════════════════════
# 规则与指纹
# ══════════════════════════════════════════════════════════════════
def rule_id(oracle_id: str, cwe: str) -> str:
    """``veripen/<oracle>/<cwe>``：不含客户名/资产/路由，语义变更不改 id（09 §2）。"""
    return f"veripen/{oracle_id}/{cwe}"


def param_signature(f: FindingView) -> str:
    return f.param_signature or "none"


def fingerprints(f: FindingView) -> dict[str, str]:
    """两个指纹各司其职（09 §3）：Finding 判"是不是同一个洞"，Route 判"面有没有变"。"""
    base = f"{rule_id(f.oracle_id, f.cwe)}|{f.asset_id}|{param_signature(f)}|1.0.0"
    with_route = f"{base}|{f.template_sha256}"
    return {
        "veripenFinding/v1": hashlib.sha256(base.encode()).hexdigest(),
        "veripenRoute/v1": hashlib.sha256(with_route.encode()).hexdigest(),
    }


def counts_by_outcome(report: ReportInput) -> dict[str, int]:
    out = {o.value: 0 for o in Outcome}
    for f in report.findings:
        out[f.outcome.value] += 1
    return out


def coverage_by_source(report: ReportInput) -> dict[str, int]:
    """覆盖来源分档（09 §4）：agent 自述永远不能算"已覆盖"。"""
    tiers = {"machine_observed": 0, "agent_reported": 0, "none": 0}
    raw = report.coverage.get("by_source") or {}
    for key in tiers:
        tiers[key] = int(raw.get(key, 0))
    return tiers


# ══════════════════════════════════════════════════════════════════
# JSON 视图
# ══════════════════════════════════════════════════════════════════
def render_json(report: ReportInput) -> dict[str, Any]:
    assert_renderable(report)
    findings = []
    for f in report.findings:
        findings.append(
            {
                "id": f.finding_id,
                "ruleId": rule_id(f.oracle_id, f.cwe),
                "cwe": f.cwe,
                "severity": f.severity,
                "confidence": f.confidence,
                "title": f.title,
                "asset": f.asset_id,
                "route": {
                    "ref": f.route_short,
                    "source": f.route_strength,
                    "strength": f.route_strength,
                    "template_sha256": f.template_sha256[:12],
                },
                "evidence": {
                    "capsule_id": f.capsule_id,
                    "self_sha256": f.self_sha256,
                    "replay_status": f.replay_status,
                    "replay_at": f.replay_at,
                },
                "outcome": f.outcome.value,
                "reason_code": f.reason_code,
                "unverified_in_image": f.sandbox_overridden,
                "fingerprints": fingerprints(f),
                "impact": f.impact,
                "advice": f.advice,
            }
        )
    return {
        "schema": "veripen.report/v1",
        "engagement": report.engagement,
        "env": {
            "profile_hash": report.profile_hash,
            "image_digest": report.image_digest,
            "resolver_version": report.resolver_version,
            "distribution": report.distribution,
        },
        "root_sha256": report.root_sha256,
        "routeset_sha256": [s[:12] for s in report.routeset_sha256],
        "sources_failed": list(report.route_sources_failed),
        "counts": {
            "total": len(report.findings),
            "by_outcome": counts_by_outcome(report),
            "by_severity": _by_severity(report),
            "by_strength": _by_strength(report),
        },
        "coverage": {
            "tiers": coverage_by_source(report),
            "zero_hit_tags": list(report.coverage.get("zero_hit_tags") or []),
            "weak_faces": [
                {"rule": rule_id(f.oracle_id, f.cwe), "ref": f.route_short}
                for f in report.findings
                if f.is_weak
            ],
            "route_causes": dict(report.coverage.get("route_causes") or {}),
        },
        "findings": findings,
        "compliance": dict(report.coverage.get("compliance") or {}),
        "frameworks": list(report.frameworks),
    }


def _by_severity(report: ReportInput) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in report.findings:
        out[f.severity] = out.get(f.severity, 0) + 1
    return dict(sorted(out.items()))


def _by_strength(report: ReportInput) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in report.findings:
        out[f.route_strength] = out.get(f.route_strength, 0) + 1
    return dict(sorted(out.items()))


# ══════════════════════════════════════════════════════════════════
# SARIF 2.1.0 视图
# ══════════════════════════════════════════════════════════════════
_SARIF_LEVEL = {"critical": "error", "high": "error", "medium": "warning", "low": "note", "info": "note"}


def render_sarif(report: ReportInput) -> dict[str, Any]:
    assert_renderable(report)
    rules: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []
    for f in report.findings:
        rid = rule_id(f.oracle_id, f.cwe)
        meta = report.rules.get(rid) or report.rules.get(f.oracle_id) or {}
        rules.setdefault(
            rid,
            {
                "id": rid,
                "name": f.oracle_id,
                "shortDescription": {"text": meta.get("short", f.oracle_id)},
                "fullDescription": {"text": meta.get("full", f.oracle_id)},
                "helpUri": meta.get("help", "about:blank"),
                "defaultConfiguration": {"level": _SARIF_LEVEL.get(f.severity, "note")},
                "properties": {
                    "tags": ["security", f"cwe/{f.cwe.split('-')[-1]}"]
                    + [f"owasp/{t}" for t in meta.get("owasp", [])],
                },
            },
        )
        results.append(
            {
                "ruleId": rid,
                "level": _SARIF_LEVEL.get(f.severity, "note"),
                "message": {"text": f.title},
                "partialFingerprints": fingerprints(f),
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": f"asset:{f.asset_id}"},
                            "region": {"startLine": 1},
                        },
                        # 语义面以 route_ref 呈现：SARIF 里也没有地址
                        "properties": {"routeRef": f.route_short, "routeStrength": f.route_strength},
                    }
                ],
                "properties": {
                    "outcome": f.outcome.value,
                    "capsuleId": f.capsule_id,
                    "selfSha256": f.self_sha256,
                    "replayStatus": f.replay_status,
                },
            }
        )
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "veripen",
                        "informationUri": "about:blank",
                        "rules": list(rules.values()),
                    }
                },
                "results": results,
                "properties": {
                    "rootSha256": report.root_sha256,
                    "distribution": report.distribution,
                },
            }
        ],
    }


# ══════════════════════════════════════════════════════════════════
# Markdown 视图
# ══════════════════════════════════════════════════════════════════
def render_markdown(report: ReportInput) -> str:
    assert_renderable(report)
    counts = counts_by_outcome(report)
    lines: list[str] = []
    w = lines.append

    w(f"# {report.engagement} 渗透验证报告")
    w("")
    w(f"- profile_hash: `{report.profile_hash[:16]}` · image: `{report.image_digest[:16]}`")
    w(f"- 报告指纹 root_sha256: `{report.root_sha256[:16]}`")
    for s in report.routeset_sha256:
        w(f"- routeset_sha256: `{s[:12]}`")
    if report.route_sources_failed:
        w(f"- **来源失败**：{', '.join(report.route_sources_failed)}（本次只用其余来源）")
    w("")
    w("## 0. 结论四态")
    w("")
    w("| 态 | 数量 | 含义 |")
    w("|---|---|---|")
    w(f"| reported | {counts['reported']} | 已验证并出报告 |")
    w(f"| undetermined | {counts['undetermined']} | 跑了但判不了 |")
    w(f"| error | {counts['error']} | 执行失败 |")
    w(f"| not_run | {counts['not_run']} | 未执行（授权/预算/跳过） |")
    w("")
    w("> 禁止把 `error` / `not_run` 写进「未发现」或「安全」的表述附近。")
    w("")

    reported = [f for f in report.findings if f.outcome is Outcome.REPORTED]
    w("## 1. 摘要")
    w("")
    w("| 严重度 | 数量 |")
    w("|---|---|")
    for sev, n in _by_severity(report).items():
        w(f"| {sev} | {n} |")
    w("")

    w("## 2. 已验证发现")
    w("")
    if not reported:
        w("本次没有产出可验证的发现。")
        w("")
    for f in reported:
        tag = " [未隔离执行]" if f.sandbox_overridden else ""
        w(f"### {f.title}{tag}")
        w("")
        w(f"- `{f.severity.upper()}` · {f.cwe} · 置信度 {f.confidence:.2f} · 证据强度 {f.route_strength}")
        w(f"- 资产 `{f.asset_id}` · 面 `{f.route_short}`（{f.route_strength}）")
        if f.situation:
            w(f"- 现象：{f.situation}")
        sha_short = (f.self_sha256 or "")[:16]
        w(f"- 证据：`capsule {f.capsule_id}` · `self_sha256 {sha_short}…` · replay `{f.replay_status}`")
        w(f"- 复现：`veripen replay --capsule out/capsules/{f.capsule_id}.json`")
        if f.impact:
            w(f"- 影响：{f.impact}")
        if f.advice:
            w(f"- 建议：{f.advice}")
        w("")

    _render_unverified(report, lines)
    _render_coverage(report, lines)

    if report.frameworks:
        w("## 5. 合规映射")
        w("")
        for fw in report.frameworks:
            w(f"- {fw}")
        w("")

    w("## 6. 附录")
    w("")
    w(f"`root_sha256[:12]` · {report.engagement} · {report.render_time} · {report.distribution}")
    return "\n".join(lines) + "\n"


def _render_unverified(report: ReportInput, lines: list[str]) -> None:
    w = lines.append
    w("## 3. 未能验证（按 outcome 分组）")
    w("")
    groups = {
        Outcome.UNDETERMINED: "未能判定（跑了但判不了）",
        Outcome.ERROR: "执行失败（没跑成，须附原因）",
        Outcome.NOT_RUN: "未执行（哪一类要写明）",
    }
    for outcome, label in groups.items():
        items = [f for f in report.findings if f.outcome is outcome]
        w(f"### {label} · {len(items)}")
        w("")
        if not items:
            w("无。")
            w("")
            continue
        for f in items:
            reason = f.reason_code or "-"
            title = TITLE_BY_STATUS.get(reason, reason)
            w(f"- `{f.asset_id}` · {f.oracle_id} · {title}（`{reason}`）")
        w("")


def _render_coverage(report: ReportInput, lines: list[str]) -> None:
    w = lines.append
    tiers = coverage_by_source(report)
    w("## 4. 覆盖范围与未覆盖")
    w("")
    w("| 覆盖来源 | 数量 | 可否单独作为「已覆盖」 |")
    w("|---|---|---|")
    w(f"| machine_observed | {tiers['machine_observed']} | 可以 |")
    w(f"| agent_reported | {tiers['agent_reported']} | **不可以**（降级为 not_run） |")
    w(f"| none | {tiers['none']} | 不可以 |")
    w("")
    zero = list(report.coverage.get("zero_hit_tags") or [])
    w("### 0 命中的标签（这才是「没打到的面」）")
    w("")
    if zero:
        for tag in zero:
            w(f"- `{tag}`")
    else:
        w("无。")
    w("")
    causes = dict(report.coverage.get("route_causes") or {})
    if causes:
        w("### 未覆盖成因（三种必须分开写）")
        w("")
        for cause, note in causes.items():
            w(f"- `{cause}`：{note}")
        w("")
    weak = [f for f in report.findings if f.is_weak]
    w("### 弱证据面清单")
    w("")
    if weak:
        for f in weak:
            w(f"- `{f.route_short}` · {f.oracle_id} · {f.route_strength}（需人工复核覆盖）")
    else:
        w("无。")
    w("")


__all__ = [
    "HIGH_SEVERITIES",
    "Outcome",
    "FindingView",
    "ROUTE_PREFIX_LEN",
    "RenderError",
    "ReportInput",
    "TITLE_BY_STATUS",
    "WEAK_STRENGTHS",
    "WORDING",
    "assert_renderable",
    "counts_by_outcome",
    "coverage_by_source",
    "fingerprints",
    "param_signature",
    "render_json",
    "render_markdown",
    "render_sarif",
    "rule_id",
]
