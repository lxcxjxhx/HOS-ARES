"""B12.2 · outcome 四态 + 覆盖来源（32 §3 A1/A2，09 §3，10 §3.0）。

`state` 说"结论是什么"，`outcome` 说"这次跑成了没有"——**两个正交维度**。
把 `error` / `not_run` 塞进 TN 或"未发现"，就是拿跑挂了冒充清白。

三条规则在这里写死，schema 与落库两侧各自强制一次：
1. ``outcome in (error, not_run)`` → ``state = BLOCKED``（跑挂了不许有结论）；
2. ``coverage_src = agent_reported`` → ``outcome = not_run``（自述不算覆盖）；
3. ``error`` 必带 ``error_reason``，``not_run`` 必带 ``not_run_reason``。
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from .models import FindingState

NOT_RUN_PREFIX = "skipped_"


class ErrorReason(str, Enum):
    """09 §3：`error` 只说"没跑成"，原因码只允许这几种（中文表见报告 §3）。"""

    TRANSPORT = "transport"
    TARGET_DOWN = "target_down"
    TOOL_CRASH = "tool_crash"
    TIMEOUT = "timeout"
    TLS = "tls"
    OOB_UNREACHABLE = "oob_unreachable"


class NotRunReason(str, Enum):
    """09 §3：`not_run` 要写明是"哪一类没跑"。"""

    SKIPPED_SCOPE = "skipped_scope"
    SKIPPED_UNRESOLVED = "skipped_unresolved"
    APPROVAL_PENDING = "approval_pending"
    BUDGET_NOT_ALLOCATED = "budget_not_allocated"


class Outcome(str, Enum):
    REPORTED = "reported"
    UNDETERMINED = "undetermined"
    ERROR = "error"
    NOT_RUN = "not_run"

    @property
    def counts_for_metrics(self) -> bool:
        """error / not_run **绝对不算**进 TP/FP/FN/TN（10 §3.0）。"""
        return self is Outcome.REPORTED or self is Outcome.UNDETERMINED

    @property
    def is_covered(self) -> bool:
        """只有真跑过才算"覆盖过这一面"；没跑成不是覆盖。"""
        return self.counts_for_metrics


class CoverageSrc(str, Enum):
    MACHINE_OBSERVED = "machine_observed"
    AGENT_REPORTED = "agent_reported"
    NONE = "none"

    @property
    def may_claim_coverage(self) -> bool:
        return self is CoverageSrc.MACHINE_OBSERVED


ERROR_REASONS: frozenset[str] = frozenset(e.value for e in ErrorReason)
NOT_RUN_REASONS: frozenset[str] = frozenset(e.value for e in NotRunReason)


class OutcomeViolation(ValueError):
    """outcome 与 state / 原因码不自洽。**不修、不猜，直接抛**。"""

    code = "VP-8002"


def validate(
    *, outcome: str | Outcome, state: str | FindingState, coverage_src: str | CoverageSrc,
    error_reason: str | None, not_run_reason: str | None,
) -> Outcome:
    """返回归一后的 outcome；任一规则不满足即抛 ``OutcomeViolation``。"""
    try:
        oc = Outcome(outcome) if not isinstance(outcome, Outcome) else outcome
        src = CoverageSrc(coverage_src) if not isinstance(coverage_src, CoverageSrc) else coverage_src
    except ValueError as exc:
        raise OutcomeViolation(str(exc)) from exc

    if oc in (Outcome.ERROR, Outcome.NOT_RUN) and state != FindingState.BLOCKED:
        raise OutcomeViolation(f"outcome={oc.value} 必须 state=BLOCKED，实际 {state}")

    if src is CoverageSrc.AGENT_REPORTED and oc is not Outcome.NOT_RUN:
        raise OutcomeViolation(
            f"coverage_src=agent_reported 必须降级为 not_run，实际 {oc.value}"
            "（自述不能单独作为「已覆盖」）"
        )

    if oc is Outcome.ERROR:
        if error_reason not in ERROR_REASONS:
            allowed = " | ".join(sorted(ERROR_REASONS))
            raise OutcomeViolation(f"outcome=error 需 error_reason ∈ ({allowed})，实际 {error_reason!r}")
    elif error_reason is not None:
        raise OutcomeViolation(f"outcome={oc.value} 不得带 error_reason")

    if oc is Outcome.NOT_RUN:
        # 规范列出的五类之外，允许 `skipped_<原因>`：SKIPPED 的理由本来就随场景长（09 §3）
        reason = not_run_reason or ""
        if not (reason in NOT_RUN_REASONS or (reason.startswith(NOT_RUN_PREFIX) and reason != NOT_RUN_PREFIX)):
            allowed = " | ".join(sorted(NOT_RUN_REASONS))
            raise OutcomeViolation(
                f"outcome=not_run 需 not_run_reason ∈ ({allowed}) 或 skipped_<原因>，实际 {not_run_reason!r}"
            )
    elif not_run_reason is not None:
        raise OutcomeViolation(f"outcome={oc.value} 不得带 not_run_reason")

    return oc


def from_row(row: Any) -> Outcome:
    """从库里一行还原 outcome；缺列/错值一律 ERROR，**不当成 reported 蒙混**。"""
    try:
        raw = row["outcome"]
    except (KeyError, IndexError, TypeError):
        raw = None
    try:
        return Outcome(str(raw or "error"))
    except ValueError:
        return Outcome.ERROR


def counts(rows: Any) -> dict[str, int]:
    """四态计数（09 §3）：四键恒在，错值记进 error，绝不丢进 reported。"""
    out = {o.value: 0 for o in Outcome}
    for row in rows:
        out[from_row(row).value] += 1
    return out


__all__ = [
    "NOT_RUN_PREFIX",
    "CoverageSrc",
    "Outcome",
    "OutcomeViolation",
    "counts",
    "from_row",
    "validate",
]
