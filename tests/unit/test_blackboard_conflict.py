"""B51 黑板冲突消解（`H03 §3` / `H07` B51）。

判据：冲突按 (来源等级, 观测时间) 取一条 live、其余 superseded 且**不删除**；
注入按打分序而非写入序；冲突表进报告且确定性。
"""

from __future__ import annotations

import pytest

from veripen.report.render import render_conflict_table
from veripen_core.blackboard import ORIGIN_RANK, Blackboard
from veripen_core.store import Store


@pytest.fixture()
def store() -> Store:
    s = Store(":memory:").init()
    yield s
    s.close()


def _bb(store: Store) -> Blackboard:
    return Blackboard(store)


# ── A 取一条 live，其余 superseded，不删除 ─────────────────────
def test_origin_rank_wins_and_losers_are_kept(store: Store) -> None:
    bb = _bb(store)
    inferred = bb.append(task_id="t1", subject="web-01", predicate="server",
                         value="apache", origin="inference", evidence_id="ev-1")
    observed = bb.append(task_id="t1", subject="web-01", predicate="server",
                         value="nginx/1.26", origin="observed", evidence_id="ev-2")
    resolutions = bb.resolve("t1")
    assert len(resolutions) == 1
    assert resolutions[0].winner.id == observed.id
    assert [f.id for f in resolutions[0].losers] == [inferred.id]
    assert len(bb.all("t1")) == 2, "落选事实必须留板（只增不改）"
    assert len(bb.live("t1")) == 2, "live 不删行，消解发生在注入视图"


def test_same_origin_newer_wins(store: Store) -> None:
    bb = _bb(store)
    old = bb.append(task_id="t1", subject="s", predicate="p", value="v1",
                    origin="tool", evidence_id="ev-1")
    new = bb.append(task_id="t1", subject="s", predicate="p", value="v2",
                    origin="tool", evidence_id="ev-2")
    winner = bb.resolve("t1")[0].winner
    assert winner.id == new.id
    assert old.id in [f.id for f in bb.resolve("t1")[0].losers]


def test_origin_rank_table_is_monotone() -> None:
    assert ORIGIN_RANK["target"] >= ORIGIN_RANK["tool"] >= ORIGIN_RANK["inference"]


# ── B 注入按打分序，且落选者不注入 ─────────────────────────────
def test_injectable_excludes_losers(store: Store) -> None:
    bb = _bb(store)
    bb.append(task_id="t1", subject="s", predicate="p", value="bad",
              origin="inference", evidence_id="ev-1")
    bb.append(task_id="t1", subject="s", predicate="p", value="good",
              origin="observed", evidence_id="ev-2")
    values = [f.value for f in bb.injectable("t1")]
    assert values == ["good"], "被压住的事实不许进上下文"


def test_injectable_ordering_is_scored_not_insertion(store: Store) -> None:
    bb = _bb(store)
    bb.append(task_id="t1", subject="a", predicate="p", value="low",
              origin="inference", evidence_id="ev-1")
    bb.append(task_id="t1", subject="b", predicate="p", value="high",
              origin="observed", evidence_id="ev-2")
    assert [f.value for f in bb.injectable("t1")] == ["high", "low"]


def test_non_conflicting_facts_survive(store: Store) -> None:
    bb = _bb(store)
    bb.append(task_id="t1", subject="s1", predicate="p", value="x", origin="observed",
              evidence_id="ev-1")
    bb.append(task_id="t1", subject="s1", predicate="q", value="y", origin="observed",
              evidence_id="ev-2")
    assert bb.resolve("t1") == []
    assert {f.value for f in bb.injectable("t1")} == {"x", "y"}


# ── C 冲突表：确定性 + 进报告 ──────────────────────────────────
def test_conflict_report_shape_and_determinism(store: Store) -> None:
    bb = _bb(store)
    bb.append(task_id="t1", subject="web-01", predicate="server", value="apache",
              origin="inference", evidence_id="ev-1")
    bb.append(task_id="t1", subject="web-01", predicate="server", value="nginx",
              origin="observed", evidence_id="ev-2")
    first = bb.conflict_report("t1")
    second = bb.conflict_report("t1")
    assert first == second
    row = first[0]
    assert set(row) == {"subject", "predicate", "winner", "losers", "reason"}
    assert row["winner"]["value"] == "nginx"
    assert row["losers"][0]["value"] == "apache"


def test_conflict_table_renders_winner_reason_losers(store: Store) -> None:
    bb = _bb(store)
    bb.append(task_id="t1", subject="web-01", predicate="server", value="apache",
              origin="inference", evidence_id="ev-1")
    bb.append(task_id="t1", subject="web-01", predicate="server", value="nginx",
              origin="observed", evidence_id="ev-2")
    text = render_conflict_table(bb.conflict_report("t1"))
    assert "nginx" in text and "apache" in text and "origin=observed" in text
    assert render_conflict_table(bb.conflict_report("t1")) == text, "两次渲染必须逐行相同"


def test_conflict_table_empty_is_explicit() -> None:
    assert render_conflict_table([]) == "本轮没有事实冲突。\n"


def test_conflict_table_has_no_address_and_no_verdict_words() -> None:
    text = render_conflict_table([
        {"subject": "web-01", "predicate": "server", "winner": {"value": "nginx", "origin": "observed"},
         "losers": [{"value": "apache", "origin": "inference"}], "reason": "origin=observed"},
    ])
    for banned in ("http", "漏洞", "VERIFIED"):
        assert banned not in text


@pytest.mark.parametrize("origin", ["observed", "tool", "inference"])
def test_resolution_reason_names_origin(store: Store, origin: str) -> None:
    bb = _bb(store)
    bb.append(task_id="t1", subject="s", predicate="p", value="a", origin="tool",
              evidence_id="ev-1")
    bb.append(task_id="t1", subject="s", predicate="p", value="b", origin=origin,
              evidence_id="ev-2")
    reason = bb.resolve("t1")[0].reason
    assert reason.startswith("origin=")
