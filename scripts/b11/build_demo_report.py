#!/usr/bin/env python3
"""B11 演示：从一条 finding 生成三视图 + manifest，然后自校验。

`make report-demo` 会跑它；输出目录 `out/b11/report/`。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from veripen.report import (  # noqa: E402
    FindingView,
    Outcome,
    ReportInput,
    build_manifest,
    counts_by_outcome,
    lint_report,
    render_json,
    render_markdown,
    render_sarif,
)


def sample() -> ReportInput:
    findings = (
        FindingView(
            finding_id="f-1", title="对象级授权缺失（BOLA）", asset_id="web-01",
            oracle_id="idor_cross_account", cwe="CWE-639", severity="high", confidence=0.95,
            route_strength="contract", route_ref="route://contract/7f3a2c11d9e04b58",
            capsule_id="9f2c" + "0" * 60, self_sha256="9f2c" + "0" * 60,
            replay_status="OK", replay_at=1761966849, template_sha256="b1e7" + "0" * 60,
            param_signature="path:id",
            situation="secondary 账号读取了 primary 拥有的对象，且访问无权对象被拒",
            impact="可读取他用户对象数据",
            advice="在对象读取路径上补齐归属校验",
        ),
        FindingView(
            finding_id="f-2", title="JWT 校验缺陷：alg=none 被接受", asset_id="web-01",
            oracle_id="jwt_suite", cwe="CWE-347", severity="medium", confidence=0.9,
            route_strength="contract", route_ref="route://contract/8c44be0a27f19d63",
            capsule_id="5c9a" + "0" * 60, self_sha256="5c9a" + "0" * 60,
            replay_status="PARTIAL", replay_at=1761966900, template_sha256="c2f8" + "0" * 60,
            param_signature="header:alg",
            situation="无效签名被拒，但 alg=none 的 token 仍被接受",
            impact="可伪造身份访问受保护数据",
            advice="强制校验签名并拒绝 alg=none",
        ),
        FindingView(
            finding_id="f-3", title="SQL 注入（布尔差分）", asset_id="web-01",
            oracle_id="sqli_bool_diff", cwe="CWE-89", severity="low", confidence=0.7,
            route_strength="probe", route_ref="route://probe/1111222233334444",
            capsule_id="aaaa" + "0" * 60, self_sha256="aaaa" + "0" * 60,
            replay_status="NEVER",
            situation="该面由词表探测得到，可能存在注入，需人工复核",
            impact="", advice="补充契约佐证后复测",
        ),
        FindingView(
            finding_id="f-4", title="GraphQL introspection", asset_id="web-01",
            oracle_id="graphql_introspection", cwe="CWE-200", severity="info",
            confidence=0.0, route_strength="contract", route_ref=None, capsule_id=None,
            self_sha256=None, outcome=Outcome.NOT_RUN, reason_code="no_selector_match",
        ),
    )
    return ReportInput(
        engagement="ENG-DEMO",
        profile_hash="7ce4" + "0" * 60,
        image_digest="dd11" + "0" * 60,
        root_sha256="0" * 64,
        routeset_sha256=["6d20" + "0" * 60],
        resolver_version="1.0.0",
        findings=findings,
        rules={"veripen/idor_cross_account/CWE-639": {
            "short": "对象级越权", "full": "对象级授权缺失（BOLA）", "owasp": ["WSTG-ATHZ-02"]}},
        coverage={
            "zero_hit_tags": ["graphql_endpoint", "content_write"],
            "by_source": {"machine_observed": 3, "agent_reported": 1},
            "route_causes": {
                "no_selector_match": "该系统不提供此测试面",
                "route_unresolved": "存在疑似面但未解析成功",
                "sources_failed": "契约不可达，本次只用观测面",
            },
        },
        frameworks=["owasp", "cwe", "iso21434"],
        distribution="internal",
        render_time="2026-11-01T00:00:00Z",
    )


def main(argv: list[str]) -> int:
    out = Path(argv[0] if argv else "out/b11/report")
    out.mkdir(parents=True, exist_ok=True)
    report = sample()

    md = render_markdown(report)
    sarif = render_sarif(report)
    js = render_json(report)
    manifest = build_manifest(markdown=md, sarif=sarif, js=js)

    (out / "report.md").write_text(md, encoding="utf-8")
    (out / "report.sarif").write_text(json.dumps(sarif, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.json").write_text(json.dumps(js, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    hits = lint_report(list(report.findings), markdown=md, sarif=sarif, js=js)
    counts = counts_by_outcome(report)
    print(f"report: findings={len(report.findings)} counts={counts}")
    print(f"lint: address_hits={len(hits)} manifest={manifest['report_sha256'][:16]}")
    for hit in hits:
        print(f"  {hit}")
    return 0 if not hits else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
