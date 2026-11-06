"""B87 · 覆盖账本：回答"漏了什么"（`H27 §2.2`）。

`findings` 回答"发现了什么"，`coverage` 回答"看过什么、每条面怎么收的"。
在客户语境里，第二个问题才决定"零发现"是不是有意义——审计读不到 SQL 注入，
分不清「测了 14 条面、都参数化了」和「压根没看」。

**两源分开存，且机器观测只能反驳、不能确认**：

- ``machine_observed``：routeset 绑定了哪些面、挑中了哪些 oracle、
  每条真的跑出了什么。这是运行时的记录，不取决于谁说了什么。
- ``agent_reported``：agent 自己声称测过的面（``Claim``）。
  它**不能**加分，只能对账——声称了却查不到执行记录 → 进 gaps，
  按 ``claim_without_observation`` 记，severity=critical。

抄的是 strix `report/coverage.py` 的这条纪律，不是它的数据结构：
我们这边"面"是 ``RouteBinding``（不透明 ref），"收口"是四态 outcome。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .routes.types import RouteBinding

COVERAGE_FILENAME = "coverage.json"
COVERAGE_SCHEMA_VERSION = 1

#: 四态 outcome（09 §4）。**BLOCKED 与 REFUTED 必须分开**：
#: "测过没问题"和"没能测"在报告里是两件事，混在一起就把没测写成干净。
OUTCOME_LABELS: dict[str, str] = {
    "reported": "Finding reported",
    "no_issue_found": "No issue identified",
    "blocked": "Could not test",
    "undetermined": "Inconclusive",
    "error": "Execution error",
}

_VERDICT_TO_OUTCOME: dict[str, str] = {
    "VERIFIED": "reported",
    "REFUTED": "no_issue_found",
    "INCONCLUSIVE": "undetermined",
    "BLOCKED": "blocked",
    "ERROR": "error",
}

#: 会让整份账本"不可当作完整"的缺口严重度。
_BLOCKING_SEVERITIES = frozenset({"warn", "critical"})

#: 低于这个置信度的面，是"猜出来的/只是看见过"，不是"契约声明的"。
_CONTRACT_CONFIDENCE = 0.85


@dataclass(frozen=True)
class Observation:
    """一条 oracle 的执行结果——机器观测，不由 agent 自述。"""

    oracle_id: str
    route_ref: str
    verdict: str
    reason: str = ""

    @property
    def outcome(self) -> str:
        return _VERDICT_TO_OUTCOME.get(self.verdict.upper(), "error")


@dataclass(frozen=True)
class Claim:
    """agent 自称覆盖过的面。**只用来对账，不用来加分。**"""

    surface: str
    risk_area: str
    oracle_id: str = ""
    evidence: str = ""


def completion_status(decision: str, reason: str) -> str:
    """把 agent 的收尾决策翻成账本口径的 ``run_status``。

    只有"agent 自己说做完了"（``converged`` + ``finished``）才算完成。
    预算耗尽（``budget``）、卡住不动（``converged`` + ``no_new_facts_3_steps``）、
    无事可做（``nothing_to_do``）都不算——账本一旦允许"看起来收尾了"就记完成，
    "完整"这个词就没有意义了。
    """
    return "completed" if decision == "converged" and reason == "finished" else decision


def _gap(
    kind: str,
    severity: str,
    detail: str,
    *,
    route_ref: str = "",
    oracle_id: str = "",
) -> dict[str, Any]:
    return {
        "kind": kind,
        "severity": severity,
        "detail": detail,
        "route_ref": route_ref,
        "oracle_id": oracle_id,
    }


def _applicable_pairs(
    bindings: Sequence[RouteBinding],
    oracle_tags: Mapping[str, Sequence[str]],
    selected: Sequence[str],
) -> list[tuple[str, str, tuple[str, ...]]]:
    """(route_ref, oracle_id, 相交标签) —— 这条面**本该**被哪个 oracle 测。"""
    pairs: list[tuple[str, str, tuple[str, ...]]] = []
    for binding in bindings:
        for oracle_id in selected:
            hit = tuple(t for t in oracle_tags.get(oracle_id, ()) if t in binding.semantic_tags)
            if hit:
                pairs.append((binding.ref, oracle_id, hit))
    return pairs


def build_coverage(
    *,
    asset_id: str,
    bindings: Sequence[RouteBinding],
    oracle_tags: Mapping[str, Sequence[str]],
    selected: Sequence[str],
    observations: Sequence[Observation],
    excluded: Sequence[str] = (),
    claims: Sequence[Claim] = (),
    run_status: str = "completed",
    exit_reason: str = "",
) -> dict[str, Any]:
    """组装覆盖账本文档。**纯函数**：不读盘、不看时钟（时间由调用方给）。"""
    executed = {(o.route_ref, o.oracle_id) for o in observations}
    pairs = _applicable_pairs(bindings, oracle_tags, selected)
    excluded_set = set(excluded)

    covered_faces = {ref for ref, _, _ in pairs}
    gaps: list[dict[str, Any]] = []

    # ① 面没有任何 oracle 能测：分母里有一条面，分子永远到不了。
    for binding in bindings:
        if binding.ref in covered_faces:
            continue
        why = "未在任何 oracle 的语义标签下" if binding.semantic_tags else "没有语义标签"
        gaps.append(
            _gap(
                "face_without_oracle",
                "critical",
                f"这条面{why}，本轮没有任何判定覆盖它（不是「测过没问题」）。",
                route_ref=binding.ref,
            )
        )

    # ② 该跑没跑：适用但没执行，也不是显式排除。
    for ref, oracle_id, hit in pairs:
        if (ref, oracle_id) in executed or oracle_id in excluded_set:
            continue
        gaps.append(
            _gap(
                "oracle_not_run",
                "critical",
                f"标签 {', '.join(hit)} 本可由 {oracle_id} 判定，但本轮没有执行它。",
                route_ref=ref,
                oracle_id=oracle_id,
            )
        )

    # ③ 显式排除：不是缺陷，但必须在账本里留名，否则"没测"会被读成"测过"。
    for oracle_id in dict.fromkeys(excluded):
        applied = any(o == oracle_id for _, o, _ in pairs)
        if applied:
            gaps.append(
                _gap(
                    "intentional_exclusion",
                    "info",
                    f"{oracle_id} 被显式排除（写类/压力类），相关面本轮**没有**判定。",
                    oracle_id=oracle_id,
                )
            )

    # ④ 跑不通 ≠ 没事：BLOCKED/INCONCLUSIVE/ERROR 三类都要进 gaps。
    for observation in observations:
        if observation.outcome == "reported":
            continue
        if observation.outcome == "no_issue_found":
            continue
        gaps.append(
            _gap(
                f"oracle_{observation.outcome}",
                "warn",
                observation.reason or f"{observation.oracle_id} 未得出结论。",
                route_ref=observation.route_ref,
                oracle_id=observation.oracle_id,
            )
        )

    # ⑤ 面本身是猜的：crawl/probe 来源置信 < 0.85，覆盖率再漂亮也建立在一个猜测上。
    for binding in bindings:
        if binding.confidence >= _CONTRACT_CONFIDENCE:
            continue
        gaps.append(
            _gap(
                "low_confidence_face",
                "info",
                f"这条面来自 {binding.source}（置信 {binding.confidence:.2f}），"
                "是推断/观察所得，不是契约声明。",
                route_ref=binding.ref,
            )
        )

    # ⑥ agent 声称 vs 机器观测：对不上就是 critical——喊过的必须被机器印证。
    observed_oracles = {o.oracle_id for o in observations}
    for claim in claims:
        if claim.oracle_id and claim.oracle_id in observed_oracles:
            continue
        gaps.append(
            _gap(
                "claim_without_observation",
                "critical",
                f"声称覆盖「{claim.surface}」/「{claim.risk_area}」，"
                "但本轮没有对应的执行记录。按未测处理。",
                oracle_id=claim.oracle_id,
            )
        )

    severity_rank = {"critical": 0, "warn": 1, "info": 2}
    gaps.sort(key=lambda g: (severity_rank.get(str(g["severity"]), 3), str(g["kind"])))

    outcome_counts: dict[str, int] = {}
    for observation in observations:
        outcome_counts[observation.outcome] = outcome_counts.get(observation.outcome, 0) + 1

    blocking = [g for g in gaps if g["severity"] in _BLOCKING_SEVERITIES]
    caveats: list[str] = []
    if run_status != "completed":
        caveats.append(f"本轮以 '{run_status}' 结束，覆盖只反映停下之前做完的部分。")
    if blocking:
        caveats.append(f"{len(blocking)} 项缺口使得本账本**不能**读作完整覆盖。")

    return {
        "schema_version": COVERAGE_SCHEMA_VERSION,
        "asset_id": asset_id,
        "summary": {
            "faces": len(bindings),
            "faces_covered": len(covered_faces),
            "oracles_selected": len(selected),
            "applicable_pairs": len(pairs),
            "executed": len(observations),
            "outcomes": {k: outcome_counts[k] for k in OUTCOME_LABELS if k in outcome_counts},
            "gaps": len(gaps),
        },
        "machine_observed": {
            "source": "runtime",
            "faces": [
                {
                    "route_ref": b.ref,
                    "template": b.template,
                    "source": b.source,
                    "confidence": b.confidence,
                    "semantic_tags": list(b.semantic_tags),
                    "usable": b.usable,
                }
                for b in bindings
            ],
            "oracles_selected": list(selected),
            "excluded": list(dict.fromkeys(excluded)),
            "observations": [
                {
                    "oracle_id": o.oracle_id,
                    "route_ref": o.route_ref,
                    "verdict": o.verdict.upper(),
                    "outcome": o.outcome,
                    "reason": o.reason,
                }
                for o in observations
            ],
        },
        "agent_reported": [
            {
                "surface": c.surface,
                "risk_area": c.risk_area,
                "oracle_id": c.oracle_id,
                "evidence": c.evidence,
                "source": "agent_reported",
            }
            for c in claims
        ],
        "completeness": {
            "complete": not blocking and run_status == "completed",
            "scan_status": run_status,
            "exit_reason": exit_reason,
            "caveats": caveats,
        },
        "gaps": gaps,
    }


def write_coverage(run_dir: Path, document: Mapping[str, Any]) -> Path:
    """把账本写进运行目录。先写临时文件再替换：半个 JSON 比没写更糟。"""
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / COVERAGE_FILENAME
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(dict(document), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    tmp.replace(path)
    return path


__all__ = [
    "COVERAGE_FILENAME",
    "COVERAGE_SCHEMA_VERSION",
    "OUTCOME_LABELS",
    "Claim",
    "Observation",
    "build_coverage",
    "completion_status",
    "write_coverage",
]
