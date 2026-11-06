"""B87 · 覆盖账本：漏了什么必须能算出来（`H27 §2.2`）。

每条断言对应一类缺口，各自能红：

| 断言 | 会红的情形 |
|---|---|
| 面没 oracle 能测 | 只报"跑了 N 个 oracle"，不提有面没人管 |
| 该跑没跑 | 预算/异常吞掉执行，账本上却像"测过" |
| BLOCKED ≠ 干净 | 把 "没能测" 并进 "没问题" |
| 显式排除要留名 | 默认不跑写类，却不写"这一类没测" |
| 声称必须被印证 | agent 说"查过 XSS"，机器没有执行记录仍算覆盖 |
| 猜出来的面要标 | crawl 0.70 的面与契约 0.90 的面混在一起算 |
"""

from __future__ import annotations

from pathlib import Path

from veripen_core.coverage import Claim, Observation, build_coverage, write_coverage
from veripen_core.routes.resolver import Resolver
from veripen_core.routes.sources.base import StaticSource
from veripen_core.routes.types import (
    AssetRef,
    RouteCandidate,
    RouteProvenance,
    RouteQuery,
)

S = chr(0x2F)


def p(*segments: str) -> str:
    return S + S.join(segments)


def _bindings(
    specs: list[tuple[str, tuple[str, ...]]],
    *,
    source: RouteProvenance = RouteProvenance.CONTRACT,
) -> list[object]:
    cands = [
        RouteCandidate(
            method="GET",
            template=template,
            param_names=("p1",) if "{" in template else (),
            source=source,
            response_shape={"semantic_tags": list(tags)},
        )
        for template, tags in specs
    ]
    rs = Resolver(sources=[StaticSource(source.value, source, cands)]).resolve(
        RouteQuery(asset=AssetRef(id="asset"), min_confidence=0.0), now=1
    )
    return list(rs.bindings)


ORACLE_TAGS = {"idor_cross_account": ("object_read",)}


def _doc(bindings: list[object], observations: list[Observation], **kw: object) -> dict:
    return build_coverage(
        asset_id="asset",
        bindings=bindings,  # type: ignore[arg-type]
        oracle_tags=ORACLE_TAGS,
        selected=["idor_cross_account"],
        observations=observations,
        **kw,  # type: ignore[arg-type]
    )


def _kinds(doc: dict) -> list[str]:
    return [g["kind"] for g in doc["gaps"]]


# ── 1. 面没有 oracle 能测 ──────────────────────────────────────────


def test_face_without_oracle_is_a_gap() -> None:
    bindings = _bindings([(p("api", "users", "{p1}"), ("object_read",)), (p("health"), ())])
    doc = _doc(bindings, [])
    kinds = _kinds(doc)
    assert "face_without_oracle" in kinds
    assert doc["summary"]["faces"] == 2
    assert doc["summary"]["faces_covered"] == 1
    assert doc["completeness"]["complete"] is False


# ── 2. 该跑没跑 ────────────────────────────────────────────────────


def test_applicable_pair_not_executed_is_a_gap() -> None:
    bindings = _bindings([(p("api", "users", "{p1}"), ("object_read",))])
    doc = _doc(bindings, [])
    assert "oracle_not_run" in _kinds(doc)
    assert doc["summary"]["applicable_pairs"] == 1
    assert doc["summary"]["executed"] == 0


def test_executed_pair_leaves_no_not_run_gap() -> None:
    bindings = _bindings([(p("api", "users", "{p1}"), ("object_read",))])
    ref = bindings[0].ref  # type: ignore[attr-defined]
    doc = _doc(bindings, [Observation(oracle_id="idor_cross_account", route_ref=ref, verdict="REFUTED")])
    assert "oracle_not_run" not in _kinds(doc)
    assert doc["summary"]["outcomes"] == {"no_issue_found": 1}


# ── 3. BLOCKED 不等于干净 ─────────────────────────────────────────


def test_blocked_is_not_recorded_as_clean() -> None:
    bindings = _bindings([(p("api", "users", "{p1}"), ("object_read",))])
    ref = bindings[0].ref  # type: ignore[attr-defined]
    doc = _doc(
        bindings,
        [Observation(oracle_id="idor_cross_account", route_ref=ref, verdict="BLOCKED", reason="无第二身份")],
    )
    assert "oracle_blocked" in _kinds(doc)
    assert doc["summary"]["outcomes"] == {"blocked": 1}
    assert doc["completeness"]["complete"] is False


# ── 4. 显式排除要留名 ──────────────────────────────────────────────


def test_excluded_oracle_is_named_not_silently_absent() -> None:
    bindings = _bindings([(p("api", "users", "{p1}"), ("object_read",))])
    doc = _doc(bindings, [], excluded=["idor_cross_account"])
    kinds = _kinds(doc)
    assert "intentional_exclusion" in kinds
    assert "oracle_not_run" not in kinds


# ── 5. 声称必须被机器印证 ──────────────────────────────────────────


def test_claim_without_observation_is_critical() -> None:
    bindings = _bindings([(p("api", "users", "{p1}"), ("object_read",))])
    doc = _doc(bindings, [], claims=[Claim(surface="登录框", risk_area="SQL 注入", oracle_id="sqli_bool_diff")])
    claim_gaps = [g for g in doc["gaps"] if g["kind"] == "claim_without_observation"]
    assert claim_gaps and claim_gaps[0]["severity"] == "critical"
    assert doc["agent_reported"][0]["risk_area"] == "SQL 注入"


def test_claim_backed_by_observation_is_not_a_gap() -> None:
    bindings = _bindings([(p("api", "users", "{p1}"), ("object_read",))])
    ref = bindings[0].ref  # type: ignore[attr-defined]
    doc = _doc(
        bindings,
        [Observation(oracle_id="idor_cross_account", route_ref=ref, verdict="REFUTED")],
        claims=[Claim(surface="用户详情", risk_area="越权", oracle_id="idor_cross_account")],
    )
    assert "claim_without_observation" not in _kinds(doc)


# ── 6. 猜出来的面要标 ──────────────────────────────────────────────


def test_crawl_face_is_flagged_low_confidence() -> None:
    bindings = _bindings([(p("api", "users", "{p1}"), ("object_read",))], source=RouteProvenance.CRAWL)
    doc = _doc(bindings, [])
    assert "low_confidence_face" in _kinds(doc)


def test_contract_face_is_not_flagged() -> None:
    bindings = _bindings([(p("api", "users", "{p1}"), ("object_read",))])
    doc = _doc(bindings, [])
    assert "low_confidence_face" not in _kinds(doc)


# ── 7. 落盘 ────────────────────────────────────────────────────────


def test_write_coverage_is_valid_json(tmp_path: Path) -> None:
    import json

    bindings = _bindings([(p("api", "users", "{p1}"), ("object_read",))])
    path = write_coverage(tmp_path, _doc(bindings, []))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["asset_id"] == "asset"
    assert path.name == "coverage.json"


def test_clean_run_is_complete() -> None:
    bindings = _bindings([(p("api", "users", "{p1}"), ("object_read",))])
    ref = bindings[0].ref  # type: ignore[attr-defined]
    doc = _doc(bindings, [Observation(oracle_id="idor_cross_account", route_ref=ref, verdict="VERIFIED")])
    assert doc["completeness"]["complete"] is True
    assert doc["gaps"] == []


# ── 8. 报告里必须点名缺口 ──────────────────────────────────────────


def test_report_section_names_the_gaps() -> None:
    """账本不能只活在侧车 JSON 里：报告第 4 节要把缺口点出来。"""
    from veripen.report.render import ReportInput, render_markdown

    bindings = _bindings([(p("api", "users", "{p1}"), ("object_read",))])
    doc = _doc(bindings, [])
    report = ReportInput(
        engagement="fixture.test",
        profile_hash="",
        image_digest="",
        root_sha256="",
        findings=[],
        coverage={"gaps": doc["gaps"], "summary": doc["summary"],
                  "completeness": doc["completeness"]},
    )
    md = render_markdown(report)
    assert "覆盖缺口" in md
    assert "oracle_not_run" in md


# ── 9. 完整度只在"真的收尾"时成立 ─────────────────────────────────


def test_completion_status_only_trusts_an_explicit_finish() -> None:
    from veripen_core.coverage import completion_status

    assert completion_status("converged", "finished") == "completed"
    assert completion_status("budget", "budget_exhausted:steps") == "budget"
    assert completion_status("converged", "no_new_facts_3_steps") == "converged"
    assert completion_status("nothing_to_do", "no_oracles") == "nothing_to_do"


def test_budget_exhaustion_makes_the_ledger_incomplete() -> None:
    bindings = _bindings([(p("api", "users", "{p1}"), ("object_read",))])
    doc = _doc(bindings, [], run_status="budget", exit_reason="budget_exhausted:steps")
    assert doc["completeness"]["complete"] is False
    assert any("budget" in c for c in doc["completeness"]["caveats"])
