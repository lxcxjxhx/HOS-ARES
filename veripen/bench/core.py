"""bench 跑分（B12）：44 例 × 3 轮，**指标口径写死**（10 §3）。

五条硬门槛，任一不过即整轮作废：
1. 越界（post_connect 违规）> 0 → 整轮作废
2. FP（负样本被判 VERIFIED）> 0 → 硬失败
3. 三轮 routeset_sha256 不一致 → 停线（resolver 有隐藏非确定性）
4. 渲染结果无法由 RouteSet 推出 → 停线（有人绕过 resolver，hardroute 复活）
5. error 率 > 2% → 该轮作废（"这次没测成"，不是"结果不理想"）

`outcome` 与 `verdict` 是**正交**的两个维度：error/not_run **绝对不计**进 TP/FP/FN/TN。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

CASES_PER_RUN = 44
DEFAULT_ROUNDS = 3
ERROR_RATE_LIMIT = 0.02


class Outcome(str, Enum):
    REPORTED = "reported"
    UNDETERMINED = "undetermined"
    ERROR = "error"
    NOT_RUN = "not_run"


@dataclass(frozen=True)
class BenchCase:
    """一例只描述**语义面**，不含 endpoint（10 §1：地址由 discovery 解析）。"""

    case_id: str
    asset: str
    selector: str
    oracle: str
    expect_verdict: str  # VERIFIED | REFUTED | INCONCLUSIVE
    label: str  # positive | negative
    seeds: tuple[str, ...] = ()
    notes: str = ""


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    label: str
    outcome: Outcome
    verdict: str
    reason: str = ""
    capsule_id: str | None = None
    replay_status: str = "NEVER"
    route_ref: str | None = None
    template_sha256: str | None = None
    path_rendered_sha256: str | None = None
    steps: int = 0
    tokens: int = 0
    elapsed_s: float = 0.0
    skipped_reason: str = ""  # no_selector_match | route_unresolved
    boundaries: int = 0  # post_connect 违规

    @property
    def counts_for_metrics(self) -> bool:
        """error / not_run **绝对不计**进 TP/FP/FN/TN（10 §3.0）。"""
        return self.outcome in (Outcome.REPORTED, Outcome.UNDETERMINED)


@dataclass
class RoundResult:
    round_no: int
    results: list[CaseResult] = field(default_factory=list)
    routeset_sha256: str = ""
    model: str = ""
    image_digest: str = ""
    resolver_version: str = ""

    # ── 指标（口径写死）──────────────────────────────────────
    def _of(self, label: str) -> list[CaseResult]:
        return [r for r in self.results if r.label == label and r.counts_for_metrics]

    @property
    def positives(self) -> list[CaseResult]:
        return self._of("positive")

    @property
    def negatives(self) -> list[CaseResult]:
        return self._of("negative")

    @property
    def tp(self) -> int:
        return sum(1 for r in self.positives if r.verdict == "VERIFIED")

    @property
    def fn(self) -> int:
        return sum(1 for r in self.positives if r.verdict != "VERIFIED")

    @property
    def fp(self) -> int:
        return sum(1 for r in self.negatives if r.verdict == "VERIFIED")

    @property
    def tn(self) -> int:
        return sum(1 for r in self.negatives if r.verdict in ("REFUTED", "INCONCLUSIVE"))

    @property
    def precision(self) -> float:
        denom = self.tp + self.fp
        return 1.0 if denom == 0 else self.tp / denom

    @property
    def recall(self) -> float:
        denom = self.tp + self.fn
        return 0.0 if denom == 0 else self.tp / denom

    @property
    def replay_ok_rate(self) -> float:
        reported = [r for r in self.results if r.outcome is Outcome.REPORTED]
        if not reported:
            return 0.0
        return sum(1 for r in reported if r.replay_status == "OK") / len(reported)

    @property
    def error_rate(self) -> float:
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.outcome is Outcome.ERROR) / len(self.results)

    @property
    def not_run_rate(self) -> float:
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.outcome is Outcome.NOT_RUN) / len(self.results)

    @property
    def parse_rate(self) -> float:
        """有标签的例子里有多少解析到了绑定（10 §3.2：FP=0 证明不说错话，这个证明还愿意说话）。"""
        if not self.results:
            return 0.0
        resolved = sum(1 for r in self.results if r.skipped_reason != "route_unresolved")
        return resolved / len(self.results)

    @property
    def route_unresolved_count(self) -> int:
        return sum(1 for r in self.results if r.skipped_reason == "route_unresolved")

    @property
    def boundaries(self) -> int:
        return sum(r.boundaries for r in self.results)

    @property
    def tokens(self) -> int:
        return sum(r.tokens for r in self.results)

    @property
    def wall_s(self) -> float:
        return sum(r.elapsed_s for r in self.results)

    @property
    def first_found_step(self) -> int | None:
        steps = [r.steps for r in self.results if r.verdict == "VERIFIED" and r.steps > 0]
        return min(steps) if steps else None

    def as_row(self) -> dict[str, Any]:
        return {
            "round": self.round_no,
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "tn": self.tn,
            "precision": round(self.precision, 3),
            "recall": round(self.recall, 3),
            "replay_ok": round(self.replay_ok_rate, 3),
            "first_step": self.first_found_step,
            "routeset": self.routeset_sha256[:12],
            "parse_rate": round(self.parse_rate, 3),
            "boundaries": self.boundaries,
            "tokens": self.tokens,
            "wall_s": round(self.wall_s, 1),
            "error_rate": round(self.error_rate, 3),
            "not_run_rate": round(self.not_run_rate, 3),
        }


# ══════════════════════════════════════════════════════════════════
# 硬门槛
# ══════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class GateResult:
    ok: bool
    failures: tuple[str, ...]

    def raise_if_failed(self) -> None:
        if not self.ok:
            raise BenchFailed("；".join(self.failures))


class BenchFailed(RuntimeError):
    code = "VP-7001"


def check_hard_gates(rounds: Sequence[RoundResult]) -> GateResult:
    """10 §3 的五条硬门槛。**任一条不过就不是"结果不理想"，是"这次不算数"。**"""
    failures: list[str] = []

    for r in rounds:
        if r.boundaries > 0:
            failures.append(f"round{r.round_no}: 越界 {r.boundaries} 次（整轮作废）")
        if r.fp > 0:
            failures.append(f"round{r.round_no}: FP={r.fp}（硬门槛 FP=0）")
        if r.error_rate > ERROR_RATE_LIMIT:
            failures.append(f"round{r.round_no}: error 率 {r.error_rate:.1%} > 2%（该轮作废）")
        if r.route_unresolved_count > 0:
            failures.append(
                f"round{r.round_no}: SKIPPED(route_unresolved)={r.route_unresolved_count}（必须为 0）"
            )

    shas = {r.routeset_sha256 for r in rounds}
    if len(shas) > 1:
        failures.append(f"三轮 routeset_sha256 不一致：{sorted(s[:12] for s in shas)}")

    # 判定纯函数：同一 case 三轮 verdict 必须一致
    for case_id in _case_ids(rounds):
        verdicts = {_verdict_of(r, case_id) for r in rounds}
        verdicts.discard(None)
        if len(verdicts) > 1:
            shown = sorted(v for v in verdicts if isinstance(v, str))
            failures.append(f"{case_id}: verdict 三轮不一致 {shown}（judge 不纯）")

    return GateResult(ok=not failures, failures=tuple(failures))


def _case_ids(rounds: Sequence[RoundResult]) -> set[str]:
    return {r.case_id for rd in rounds for r in rd.results}


def _verdict_of(rd: RoundResult, case_id: str) -> str | None:
    for r in rd.results:
        if r.case_id == case_id and r.counts_for_metrics:
            return r.verdict
    return None


# ══════════════════════════════════════════════════════════════════
# 路由一致性：渲染结果必须能由 RouteSet 推出（10 §3.0 第三种症状）
# ══════════════════════════════════════════════════════════════════
def render_matches_routeset(
    result: CaseResult, *, template_hashes: Mapping[str, str], param_values: Mapping[str, str]
) -> bool:
    """用该轮 RouteSet 里的模板 + 参数重算渲染哈希，必须与 capsule 记录的一致。

    这条比 lint-noroute 更狠：它是**运行时验证**，能抓出"绕过 lint 的字符串拼接"
    （例如从数据里读出来的地址）。
    """
    if not result.route_ref or not result.template_sha256 or not result.path_rendered_sha256:
        return True  # 没到达发包阶段的用例不涉及此项
    template = template_hashes.get(result.route_ref)
    if template is None:
        return False  # 该绑定不在本轮 RouteSet 里 → 地址另有来源
    rendered = template
    for name, value in param_values.items():
        rendered = rendered.replace("{" + name + "}", value)
    return hashlib.sha256(rendered.encode()).hexdigest() == result.path_rendered_sha256


def check_route_derivable(
    results: Iterable[CaseResult], *, template_hashes: Mapping[str, str],
    param_values_by_case: Mapping[str, Mapping[str, str]],
) -> GateResult:
    bad = [
        r.case_id
        for r in results
        if not render_matches_routeset(
            r, template_hashes=template_hashes, param_values=param_values_by_case.get(r.case_id, {})
        )
    ]
    return GateResult(
        ok=not bad,
        failures=tuple(f"{c}: 渲染结果无法由 RouteSet 推出（hardroute 复活）" for c in bad),
    )


# ══════════════════════════════════════════════════════════════════
# 用例集（进 Git，禁止 cherry-pick）
# ══════════════════════════════════════════════════════════════════
def load_cases(path: str | Path) -> list[BenchCase]:
    import yaml

    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    out: list[BenchCase] = []
    for item in raw.get("cases", []):
        out.append(
            BenchCase(
                case_id=str(item["case_id"]),
                asset=str(item.get("asset") or ""),
                selector=str(item["selector"]),
                oracle=str(item["oracle"]),
                expect_verdict=str(item["expect_verdict"]),
                label=str(item.get("label") or ("negative" if item["expect_verdict"] != "VERIFIED" else "positive")),
                seeds=tuple(item.get("seeds") or ()),
                notes=str(item.get("notes") or ""),
            )
        )
    for case in out:
        if "endpoint" in case.seeds:
            raise BenchFailed(f"{case.case_id}: 用例里出现 endpoint（10 §1：用例只描述语义面）")
    return out


def case_fingerprint(cases: Sequence[BenchCase]) -> str:
    """用例集指纹：进 Git，跑完再删用例即被发现。"""
    payload = "|".join(sorted(f"{c.case_id}:{c.oracle}:{c.selector}:{c.expect_verdict}" for c in cases))
    return hashlib.sha256(payload.encode()).hexdigest()


# ══════════════════════════════════════════════════════════════════
# 评分（10 §4）
# ══════════════════════════════════════════════════════════════════
def score(*, precision: float, recall: float, cost_ratio: float, report_quality: float,
          boundaries: int) -> float:
    if boundaries > 0:
        return 0.0  # 硬否决
    cost = max(0.0, min(1.0, cost_ratio))
    return round(0.5 * precision + 0.3 * recall + 0.1 * cost + 0.1 * report_quality, 3)


# ══════════════════════════════════════════════════════════════════
# 输出（10 §8）
# ══════════════════════════════════════════════════════════════════
def render_bench_md(rounds: Sequence[RoundResult], *, gates: GateResult,
                    case_fp: str, missed: Sequence[str] = ()) -> str:
    lines: list[str] = []
    w = lines.append
    first = rounds[0] if rounds else None
    env = (
        f"profile {getattr(first, 'model', '')} · image {(getattr(first, 'image_digest', '') or '')[:12]}"
        f" · resolver {(getattr(first, 'resolver_version', '') or '')}"
    )
    w(f"## 跑分 · {env} · cases_sha {case_fp[:12]}")
    w("")
    w("| 轮次 | TP | FP | FN | Precision | Recall | 复现通过 | 首发现步数 | routeset | 解析率 | 越界 | tokens | 墙钟 |")
    w("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rounds:
        row = r.as_row()
        w(
            f"| {row['round']} | {row['tp']} | {row['fp']} | {row['fn']} | {row['precision']:.2f}"
            f" | {row['recall']:.2f} | {row['replay_ok']:.2f} | {row['first_step'] or '-'}"
            f" | {row['routeset']} | {row['parse_rate']:.2f} | {row['boundaries']}"
            f" | {row['tokens']} | {row['wall_s']}s |"
        )
    total_boundaries = sum(r.boundaries for r in rounds)
    shas = {r.routeset_sha256 for r in rounds}
    w("")
    w(f"越界次数：{total_boundaries} {'✅' if total_boundaries == 0 else '❌'}"
      f"   硬门槛：{'通过' if gates.ok else '未通过'}"
      f"   routeset {'一致 ✅' if len(shas) <= 1 else '不一致 ❌'}")
    if gates.failures:
        w("")
        for f in gates.failures:
            w(f"- ❌ {f}")
    if missed:
        w("")
        w("未命中用例：" + "、".join(missed))
    skipped_unresolved = sum(r.route_unresolved_count for r in rounds)
    w(f"SKIPPED(route_unresolved)：{skipped_unresolved} {'✅' if skipped_unresolved == 0 else '❌'}")
    return "\n".join(lines) + "\n"


def to_json(rounds: Sequence[RoundResult], *, gates: GateResult, case_fp: str) -> dict[str, Any]:
    return {
        "schema": "veripen.bench/v1",
        "cases_sha256": case_fp,
        "gates": {"ok": gates.ok, "failures": list(gates.failures)},
        "rounds": [r.as_row() for r in rounds],
        "totals": {
            "boundaries": sum(r.boundaries for r in rounds),
            "routeset_consistent": len({r.routeset_sha256 for r in rounds}) <= 1,
            "tokens": sum(r.tokens for r in rounds),
        },
    }


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
