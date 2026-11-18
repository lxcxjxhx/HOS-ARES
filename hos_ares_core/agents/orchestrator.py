"""B12.5 · 多 agent 编排：**分工不协商**（21 §3.5）。

三类角色，边界用类型焊死，不靠文字纪律：

| 角色 | 只做 | 产物 | 结构上拿不到 |
|---|---|---|---|
| Recon | 读黑板 + routeset 摘要，说"下一步测什么" | ``list[TaskSuggestion]`` | 判定函数、finding 写入口 |
| Verifier | 跑 oracle，产出 judgement | ``Judgement``（不可变） | 规划权、报告权 |
| Reporter | 汇总 finding → 视图 | ``Report`` | 判定函数、改 verdict 的入口 |

**一句话：agent 之间只传"任务"，不传"结论"。**
``TaskSuggestion`` 里没有任何结论字段——要协商，先得改 schema，那是 review 时一眼可见的改动。

并行只买速度，不买结论：同一批任务串行跑与并行跑，findings 集合必须逐字相同，
**请求数也必须相同**（并行不等于加压）。
"""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

from ..outcome import Outcome


class AgentError(RuntimeError):
    """角色越界：Recon 想判定、Reporter 想改 verdict、或产物里混进了结论字段。"""

    code = "VP-8005"


#: 结论字段。**任何**跨角色产物里出现这些键都算协商（21 §3.5.1）。
VERDICT_KEYS: frozenset[str] = frozenset(
    {"verdict", "severity", "confidence", "state", "outcome", "cwe"}
)


class Role(str, Enum):
    RECON = "recon"
    VERIFIER = "verifier"
    REPORTER = "reporter"


# ══════════════════════════════════════════════════════════════════
# Recon 的产物：**没有结论字段**
# ══════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class TaskSuggestion:
    """下一步该测什么。字段刻意少到无法表达"我觉得这是洞"。"""

    oracle_id: str
    route_ref: str
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"oracle_id": self.oracle_id, "route_ref": self.route_ref, "reason": self.reason}

    def assert_no_verdict(self) -> None:
        leaked = VERDICT_KEYS & set(self.as_dict())
        if leaked:
            raise AgentError(f"TaskSuggestion 出现结论字段 {sorted(leaked)}（分工不协商）")


class Recon:
    """只产任务，不跑、不判、不写 finding。

    规划冲突由 ``08 §1.2 NEXT_RULES`` 条件表裁决——确定性、可单测，
    而不是"再叫一个 manager agent 来拍板"（那等于把协商换了层皮）。
    """

    name = Role.RECON.value
    reads_verdict = False

    def suggest(
        self, *, candidates: Sequence[tuple[str, str]], waves: Mapping[str, str] | None = None
    ) -> list[TaskSuggestion]:
        """``candidates`` 是 ``(oracle_id, route_ref)``。按 wave 再按 oracle 排序后原样转成任务。

        **不做取舍**：取舍是 Verifier 跑完才知道的事，Recon 提前裁掉就是替判定做决定。
        """
        waves = waves or {}
        ordered = sorted(candidates, key=lambda c: (waves.get(c[0], "Z"), c[0], c[1]))
        out = [TaskSuggestion(oracle_id=o, route_ref=r, reason="wave_order") for o, r in ordered]
        for s in out:
            s.assert_no_verdict()
        return out


# ══════════════════════════════════════════════════════════════════
# Verifier 的产物：唯一能带结论的地方
# ══════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class FindingRecord:
    """判定落成 finding 的最小形态。只有 Verifier 能造它。"""

    oracle_id: str
    route_ref: str
    verdict: str
    capsule_id: str | None
    outcome: Outcome = Outcome.REPORTED
    error_reason: str | None = None

    @property
    def key(self) -> str:
        return f"{self.oracle_id}|{self.route_ref}|{self.verdict}|{self.capsule_id or '-'}"


class Verifier:
    """执行 oracle 的唯一入口。``judge`` 由外部注入，**Recon/Reporter 拿不到**。"""

    name = Role.VERIFIER.value
    reads_verdict = True

    def __init__(self, run: Callable[[TaskSuggestion], FindingRecord]) -> None:
        self._run = run

    def execute(self, suggestion: TaskSuggestion) -> FindingRecord:
        suggestion.assert_no_verdict()
        return self._run(suggestion)


# ══════════════════════════════════════════════════════════════════
# Reporter：只读汇总，**不许改 verdict**
# ══════════════════════════════════════════════════════════════════
class Reporter:
    name = Role.REPORTER.value
    reads_verdict = True

    def summarize(self, findings: Sequence[FindingRecord]) -> dict[str, Any]:
        by_outcome = {o.value: 0 for o in Outcome}
        by_verdict: dict[str, int] = {}
        for f in findings:
            by_outcome[f.outcome.value] += 1
            if f.outcome is Outcome.REPORTED:
                by_verdict[f.verdict] = by_verdict.get(f.verdict, 0) + 1
        # 报告只呈现，不重算：verdict 原样搬运，集合指纹用于比对
        return {
            "total": len(findings),
            "by_outcome": by_outcome,
            "by_verdict": dict(sorted(by_verdict.items())),
            "findings_sha256": findings_sha(findings),
        }

    def rewrite_verdict(self, *_args: object, **_kw: object) -> None:
        """显式存在但永远抛：让"Reporter 改了 verdict"这件事在调用点就崩。"""
        raise AgentError("Reporter 不得改 verdict（21 §3.5.1）")


def findings_sha(findings: Sequence[FindingRecord]) -> str:
    """findings 集合指纹：**排序后**取，与执行顺序无关。"""
    payload = "|".join(sorted(f.key for f in findings))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ══════════════════════════════════════════════════════════════════
# 编排：串行 vs 并行，只买速度
# ══════════════════════════════════════════════════════════════════
@dataclass
class RunStats:
    requests: int = 0
    tokens: int = 0
    errors: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"requests": self.requests, "tokens": self.tokens, "errors": list(self.errors)}


@dataclass
class OrchestrationResult:
    findings: list[FindingRecord] = field(default_factory=list)
    stats: RunStats = field(default_factory=RunStats)
    report: dict[str, Any] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        return findings_sha(self.findings)


def _collect(
    verifier: Callable[[TaskSuggestion], FindingRecord],
    suggestions: Sequence[TaskSuggestion],
    on_request: Callable[[], None],
    on_token: Callable[[int], None],
) -> list[FindingRecord]:
    out: list[FindingRecord] = []
    for s in suggestions:
        on_request()
        on_token(1)
        out.append(verifier(s))
    return out


def orchestrate(
    candidates: Sequence[tuple[str, str]],
    *,
    run: Callable[[TaskSuggestion], FindingRecord],
    waves: Mapping[str, str] | None = None,
    parallel: bool = True,
    max_workers: int = 2,
    per_asset_concurrency: int = 2,
) -> OrchestrationResult:
    """Recon → [Verifier…] → Reporter。

    ``per_asset_concurrency`` 是硬上限（02 §policy 默认 2）：
    **多 agent 不能用来绕限流**，所以 worker 数取二者较小值。
    """
    workers = max(1, min(max_workers, per_asset_concurrency))
    suggestions = Recon().suggest(candidates=candidates, waves=waves)
    verifier = Verifier(run)
    requests = {"n": 0}
    tokens = {"n": 0}

    def bump_req() -> None:
        requests["n"] += 1

    def bump_tok(n: int) -> None:
        tokens["n"] += n

    if parallel and workers > 1:
        # 并行只改变**顺序**，不改变每个任务的输入：同输入同输出
        def one(s: TaskSuggestion) -> FindingRecord:
            bump_req()
            bump_tok(1)
            return verifier.execute(s)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            findings = list(pool.map(one, suggestions))
    else:
        findings = _collect(verifier.execute, suggestions, bump_req, bump_tok)

    errors = tuple(
        f"{f.oracle_id}@{f.route_ref}: {f.error_reason or 'error'}"
        for f in findings
        if f.outcome is Outcome.ERROR
    )
    result = OrchestrationResult(
        findings=findings,
        stats=RunStats(requests=requests["n"], tokens=tokens["n"], errors=errors),
    )
    result.report = Reporter().summarize(findings)
    return result


def assert_abi_equal(single: OrchestrationResult, multi: OrchestrationResult) -> None:
    """单/多 agent 的生死线（21 §3.5.4 第 2、3 条）。

    结论集合必须逐字相同；**请求数也必须相同**——并行不等于加压。
    任一条不成立即抛，不给"跑得快点就凑合"留后门。
    """
    if single.fingerprint != multi.fingerprint:
        only_s = set(f.key for f in single.findings) - set(f.key for f in multi.findings)
        only_m = set(f.key for f in multi.findings) - set(f.key for f in single.findings)
        raise AgentError(
            f"单/多 agent findings 不一致：单有 {sorted(only_s)}，多有 {sorted(only_m)}"
        )
    if single.stats.requests != multi.stats.requests:
        raise AgentError(
            f"请求数不同：single={single.stats.requests} multi={multi.stats.requests}"
            "（并行不等于加压）"
        )
    if multi.stats.tokens > int(single.stats.tokens * 1.3) + 1:
        raise AgentError(
            f"多 agent 成本失控：multi={multi.stats.tokens} > 1.3 × single={single.stats.tokens}"
        )


def isolate_crash(
    suggestions: Sequence[TaskSuggestion],
    *,
    run: Callable[[TaskSuggestion], FindingRecord],
    crash_on: str,
) -> OrchestrationResult:
    """崩溃隔离（21 §3.5.4 第 4 条）：让指定 oracle 抛异常，其余照跑。

    崩掉的那条标 ``outcome=error`` 且带原因码——**不是"没发现"，是"没跑成"**。
    """
    def guarded(s: TaskSuggestion) -> FindingRecord:
        if s.oracle_id == crash_on:
            raise RuntimeError("verifier crashed")
        return run(s)

    def safe(s: TaskSuggestion) -> FindingRecord:
        try:
            return guarded(s)
        except Exception as exc:  # 崩溃面：只有这里允许吞，且必须记账
            return FindingRecord(
                oracle_id=s.oracle_id,
                route_ref=s.route_ref,
                verdict="BLOCKED",
                capsule_id=None,
                outcome=Outcome.ERROR,
                error_reason="tool_crash",
            )

    findings = [safe(s) for s in suggestions]
    return OrchestrationResult(
        findings=findings,
        stats=RunStats(
            requests=len(findings),
            errors=tuple(f"{f.oracle_id}@{f.route_ref}" for f in findings
                         if f.outcome is Outcome.ERROR),
        ),
    )


def to_json(result: OrchestrationResult) -> str:
    return json.dumps(
        {
            "findings": [f.__dict__ | {"outcome": f.outcome.value} for f in result.findings],
            "stats": result.stats.as_dict(),
            "report": result.report,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


__all__ = [
    "VERDICT_KEYS",
    "AgentError",
    "FindingRecord",
    "OrchestrationResult",
    "Recon",
    "Reporter",
    "Role",
    "RunStats",
    "TaskSuggestion",
    "Verifier",
    "assert_abi_equal",
    "findings_sha",
    "isolate_crash",
    "orchestrate",
    "to_json",
]
