"""报告措辞 lint + 报告清单（manifest）校验（B11）。

两件事：
1. **措辞纪律**（09 §4）：状态决定允许/禁止的动词。`ROUTE_UNRESOLVED → 已修复` 是最常见的误报。
2. **防手改**：报告生成时算 manifest（每节 sha256 + 总哈希），改一个字符即校验失败。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from .render import WEAK_STRENGTHS, WORDING, FindingView

#: 网络定位字面量（报告正文必须零命中，09 §6 的"地址剥离检查"同源）
_ADDRESS = re.compile(
    r"[a-zA-Z][a-zA-Z0-9+.\-]*://"
    r"|\b[0-9]{1,3}(\.[0-9]{1,3}){3}\b"
    r"|\b(localhost|0\.0\.0\.0)\b"
    # 端口：``:8080`` 这类；但 ``12:00:00`` 的分钟/秒不是端口（前一个字符是数字且更前是冒号）
    r"|(?<=[a-zA-Z]):[0-9]{2,5}(?![0-9])"
)
#: 允许出现的字面量：我们自己的不透明引用 + SARIF 规范要求的 ``$schema``
ALLOWED_SCHEMES = (
    "route://",
    "vault://",
    "op://",
    "file:///",
    "about:",
    "https://json.schemastore.org/sarif-2.1.0.json",
)


@dataclass(frozen=True)
class LintHit:
    where: str
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.where}: {self.rule}: {self.detail}"


def lint_text(text: str, *, where: str = "body") -> list[LintHit]:
    """零地址检查：去掉允许的 scheme 后仍命中即违规。"""
    hits: list[LintHit] = []
    scrubbed = text
    # 白名单是**最长匹配优先**：先把完整字面量替掉，再扫剩余文本
    for literal in sorted(ALLOWED_SCHEMES, key=len, reverse=True):
        scrubbed = scrubbed.replace(literal, "<ok>")
    for match in _ADDRESS.finditer(scrubbed):
        hits.append(LintHit(where, "address", match.group(0)[:40]))
    return hits


def lint_wording(findings: list[FindingView]) -> list[LintHit]:
    """状态 → 允许/禁止的词。只检查"现象/影响/建议"这三处人写的文本。"""
    hits: list[LintHit] = []
    for f in findings:
        key = f.wording_key or _wording_key(f)
        rule = WORDING.get(key)
        if rule is None:
            continue
        allow = tuple(rule["allow"])
        deny = tuple(rule["deny"])
        # 只检查**结论性文本**：标题是"叫什么"，不是"断言什么"，不参与措辞判定
        claims = [f.situation, f.impact, f.advice]
        blob = " ".join(claims)
        for word in deny:
            if word in blob:
                hits.append(
                    LintHit(f.finding_id, f"wording/{key}", f"出现禁用词「{word}」")
                )
        # 未验证的 finding 若**没有**任何人写文本，就没有措辞问题（它只在 §3 列原因码）
        has_text = bool("".join(claims).strip())
        if has_text and not any(word in blob for word in allow) and key != "verified_strong":
            # 弱证据/未验证的被发现：至少要有一个"可能/需复核/不可测"的限定词
            hits.append(
                LintHit(f.finding_id, f"wording/{key}", "缺少限定措辞（例如'需人工复核'）")
            )
    return hits


def _wording_key(f: FindingView) -> str:
    if f.outcome.value != "reported":
        return {
            "undetermined": "unverifiable",
            "error": "unverifiable",
            "not_run": "blocked",
        }.get(f.outcome.value, "unverifiable")
    if f.route_strength in WEAK_STRENGTHS:
        return "verified_weak"
    if f.replay_status == "ROUTE_UNRESOLVED":
        return "route_unresolved"
    if f.replay_status == "ROUTE_DRIFT":
        return "route_drift"
    return "verified_strong"


def lint_report(findings: list[FindingView], *, markdown: str, sarif: dict[str, Any],
                js: dict[str, Any]) -> list[LintHit]:
    hits = lint_wording(findings)
    hits += lint_text(markdown, where="markdown")
    hits += lint_text(json.dumps(sarif, ensure_ascii=False), where="sarif")
    hits += lint_text(json.dumps(js, ensure_ascii=False), where="json")
    return hits


# ══════════════════════════════════════════════════════════════════
# 报告清单（防手改）
# ══════════════════════════════════════════════════════════════════
def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def dumps_artifact(obj: Any) -> str:
    """**落盘用**的 JSON 序列化——与写文件、与算哈希必须是同一份字符串（B316）。

    以前 manifest 用 `sort_keys=True` 算哈希、文件用 `indent=2` 无排序写出，
    于是 `sha256sum findings.json` 与 manifest 里的值永远对不上——
    客户按常规办法验，只会得出"文件被改过"的结论。
    """
    return json.dumps(obj, ensure_ascii=False, indent=2)


def build_manifest(*, markdown: str, sarif: dict[str, Any], js: dict[str, Any]) -> dict[str, Any]:
    """每节 sha256 + 总哈希 + **逐文件哈希**。改一个字符即校验失败。

    `files` 里的值可以直接用 `sha256sum` 对盘上文件核（B316）：
    哈希算的就是落盘那一串字节（文本按 `
` 换行写，不做平台翻译）。
    """
    sarif_text = dumps_artifact(sarif)
    js_text = dumps_artifact(js)
    sections = {
        "markdown": _sha(markdown),
        "sarif": _sha(sarif_text),
        "json": _sha(js_text),
    }
    total = _sha("".join(sections[k] for k in sorted(sections)))
    files = {
        "report.md": sections["markdown"],
        "findings.json": sections["json"],
        "report.sarif": sections["sarif"],
    }
    return {"version": 1, "sections": sections, "files": files, "report_sha256": total}


def verify_manifest(manifest: Mapping[str, Any], *, markdown: str, sarif: dict[str, Any],
                    js: dict[str, Any]) -> tuple[bool, list[str]]:
    actual = build_manifest(markdown=markdown, sarif=sarif, js=js)
    problems: list[str] = []
    for key, want in (manifest.get("sections") or {}).items():
        got = actual["sections"].get(key)
        if got != want:
            problems.append(f"{key} 被改动")
    if manifest.get("report_sha256") != actual["report_sha256"]:
        problems.append("report_sha256 不符（手改或结构变化）")
    return (not problems), problems


# ══════════════════════════════════════════════════════════════════
# 质量分（09 §7，bench 的 0.1 权重）
# ══════════════════════════════════════════════════════════════════
def quality_score(findings: list[FindingView], *, hits: list[LintHit],
                  coverage: Mapping[str, Any], compliance: Mapping[str, Any]) -> float:
    reported = [f for f in findings if f.outcome.value == "reported"]
    has_capsule = all(f.capsule_id and f.replay_status != "NEVER" for f in reported) if reported else True
    score = 0.0
    score += 0.3 if has_capsule else 0.0
    score += 0.2 if not hits else 0.0
    score += 0.2 if coverage and "zero_hit_tags" in coverage else 0.0
    score += 0.2 if compliance else 0.0
    return round(score, 2)


__all__ = [
    "LintHit",
    "build_manifest",
    "lint_report",
    "lint_text",
    "lint_wording",
    "quality_score",
    "verify_manifest",
]
