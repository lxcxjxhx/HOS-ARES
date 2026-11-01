"""报告三视图（B11）：MD / SARIF / JSON 从同一份数据单向渲染。"""

from __future__ import annotations

from .lint import (
    LintHit,
    build_manifest,
    lint_report,
    lint_text,
    lint_wording,
    quality_score,
    verify_manifest,
)
from .render import (
    FindingView,
    Outcome,
    RenderError,
    ReportInput,
    assert_renderable,
    counts_by_outcome,
    coverage_by_source,
    fingerprints,
    render_json,
    render_markdown,
    render_sarif,
    rule_id,
)

__all__ = [
    "FindingView",
    "LintHit",
    "Outcome",
    "RenderError",
    "ReportInput",
    "assert_renderable",
    "build_manifest",
    "counts_by_outcome",
    "coverage_by_source",
    "fingerprints",
    "lint_report",
    "lint_text",
    "lint_wording",
    "quality_score",
    "render_json",
    "render_markdown",
    "render_sarif",
    "rule_id",
    "verify_manifest",
]
