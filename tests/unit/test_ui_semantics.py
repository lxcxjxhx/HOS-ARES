"""B35 展示语义层（H06 §2 / H08 A13–A14）。

三条硬规则，每条都有反向断言：
1. 未验证行不进 findings 默认视图、不进 now/week/low 队列、不输出 severity（D1 的界面化）；
2. 命中四态（零/单/多/未执行）与绑定态必须可区分，空值不许渲染成空白；
3. 判定公式与降噪公式不动 —— 这一层只改"呈现"，不改结论。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from web import ConsoleState, Queue, Row, build_app, findings_view

SLASH = chr(0x2F)
UI = SLASH + "ui" + SLASH
ROOT = Path(__file__).resolve().parents[2]


def _row(**kw) -> Row:  # noqa: ANN003
    base = dict(
        finding_id="f-1",
        title="对象级授权缺失",
        severity="high",
        oracle_id="idor_cross_account",
        asset_id="web-01",
        confidence=0.95,
        route_strength="contract",
        route_ref="route://contract/7f3a2c11d9e04b58",
        replay_status="OK",
        template_sha256="b" * 64,
        capsule_id="c" * 64,
        hits=1,
    )
    base.update(kw)
    return Row(**base)


# ── A13 界面不说谎 ─────────────────────────────────────────────
def test_honesty_blocked_never_reads_as_high() -> None:
    row = _row(finding_id="f-blocked", outcome="undetermined",
               reason_code="BLOCKED: baseline_abnormal", severity="high", hits=0)
    assert row.verdict == "blocked"
    assert row.queue is not Queue.NOW
    assert row.queue is Queue.UNVERIFIED


def test_honesty_unverified_excluded_from_findings() -> None:
    rows = [
        _row(finding_id="f-ok"),
        _row(finding_id="f-blocked", outcome="undetermined", reason_code="BLOCKED: x", severity="high"),
        _row(finding_id="f-incon", outcome="undetermined", reason_code="INCONCLUSIVE: three_way_failed"),
    ]
    view = findings_view(rows)
    ids = [r["id"] for r in view["rows"]]
    assert ids == ["f-ok"], "未验证行不得出现在已确认列表"
    assert view["unverified"] == 2
    assert view["queues"]["unverified"] == 2
    # now 只剩那条已验证的 high；两条未验证的都归到 unverified
    assert view["queues"]["now"] == 1


def test_honesty_severity_only_for_verified() -> None:
    rows = [
        _row(finding_id="f-ok"),
        _row(finding_id="f-blocked", outcome="undetermined", reason_code="BLOCKED: x", severity="critical"),
    ]
    view = findings_view(rows, include_low=True)
    assert {r["id"]: r["severity"] for r in view["rows"]} == {"f-ok": "high"}
    # 未验证行即便 severity 被上游填了 critical，也不进 rows（因此不会渲染成高危）
    assert all(r["id"] != "f-blocked" for r in view["rows"])


def test_honesty_verdict_mapping_is_total() -> None:
    cases = {
        "reported": "verified",
        "not_run": "not_run",
        "error": "error",
        "blocked": "blocked",
    }
    for outcome, verdict in cases.items():
        assert _row(outcome=outcome).verdict == verdict
    assert _row(outcome="undetermined", reason_code="INCONCLUSIVE: x").verdict == "inconclusive"
    assert _row(outcome="undetermined", reason_code="BLOCKED: x").verdict == "blocked"


# ── A14 空值四态可分 ───────────────────────────────────────────
@pytest.mark.parametrize(
    ("hits", "state"),
    [(0, "zero"), (1, "single"), (3, "multi"), (None, "not_run")],
)
def test_semantics_hits_state(hits: int | None, state: str) -> None:
    assert _row(hits=hits).hits_state == state


def test_semantics_four_states_are_distinct() -> None:
    states = {_row(hits=h).hits_state for h in (0, 1, 2, None)}
    assert len(states) == 4, "零/单/多/未执行必须是四种可区分状态"


def test_semantics_bound_flag() -> None:
    assert _row(route_ref=None).bound is False
    assert _row(route_ref="route://contract/7f3a2c11").bound is True
    view = findings_view([_row(route_ref=None)])
    assert view["rows"][0]["bound"] is False and view["rows"][0]["route"] == ""


def test_semantics_frontend_renders_three_way() -> None:
    """前端必须把 未绑定 / 0 / 未执行 分开渲染（静态扫描，防回归）。"""
    page = (ROOT / "web" / "static" / "index.html").read_text(encoding="utf-8")
    assert "未绑定" in page
    assert "未执行" in page
    assert "hits_state" in page
    assert "row.severity" in page and '"—"' in page, "severity 为空时必须走占位分支"


# ── 契约与不变量 ───────────────────────────────────────────────
def test_semantics_payload_version_and_read_only() -> None:
    client = TestClient(build_app(ConsoleState(findings=[_row()])))
    body = client.get(UI + "findings").json()
    assert body["payload_version"] == 2
    assert client.post(UI + "findings").status_code == 405


def test_semantics_queue_logic_unchanged_for_verified() -> None:
    """降噪公式不动：已验证行的分桶与优先级与 B15 一致。"""
    assert _row(severity="critical").queue is Queue.NOW
    assert _row(severity="medium").queue is Queue.WEEK
    assert _row(severity="info", route_strength="probe").queue is Queue.LOW
    assert _row(severity="high").priority == 3 * 3 + 2 * 1 + 1 * 1
