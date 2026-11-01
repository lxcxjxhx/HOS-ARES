"""bench（B12）：44 例 × 3 轮，指标口径写死、硬门槛不可调宽。"""

from __future__ import annotations

from .core import (
    CASES_PER_RUN,
    DEFAULT_ROUNDS,
    ERROR_RATE_LIMIT,
    BenchCase,
    BenchFailed,
    CaseResult,
    GateResult,
    Outcome,
    RoundResult,
    case_fingerprint,
    check_hard_gates,
    check_route_derivable,
    load_cases,
    render_bench_md,
    render_matches_routeset,
    score,
    to_json,
)

__all__ = [
    "CASES_PER_RUN",
    "DEFAULT_ROUNDS",
    "ERROR_RATE_LIMIT",
    "BenchCase",
    "BenchFailed",
    "CaseResult",
    "GateResult",
    "Outcome",
    "RoundResult",
    "case_fingerprint",
    "check_hard_gates",
    "check_route_derivable",
    "load_cases",
    "render_bench_md",
    "render_matches_routeset",
    "score",
    "to_json",
]
