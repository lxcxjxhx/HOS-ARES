"""B560 · 黑板冲突消解：**落选的东西不许删**，且冲突要能进报告。

`blackboard.py#resolve` 的判据：

| 判据 | 为什么容易假绿 |
|---|---|
| 同 (subject,predicate) 两个值 → 不合并、标 disputed | 合并了更"干净"，但就丢掉"当时以为是两条"这件事 |
| `observed` 高于 `inference` | 来源等级是这套东西的核心：观测压过推断 |
| 同来源 → `created_at` 新者胜 | 新鲜度 |
| **同毫秒 → 按落库顺序（rowid）**，不按 ULID 字典序 | 同毫秒的 ULID 随机后缀不可比，按它排等于随机 |
| **落选事实不删** | 审计要能看"当时以为是什么" |
| 冲突进报告 | 只在内存里消解、报告里看不到，等于没发生 |

先写判据、再写量具（B558/B559 的顺序，两轮都一次抓出真东西）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hos_ares_core.blackboard import Blackboard, Fact, ORIGIN_RANK  # noqa: E402
from hos_ares_core.store import Store  # noqa: E402


def _bb() -> tuple[Blackboard, Store]:
    store = Store(":memory:").init()
    return Blackboard(store), store


#: 可控时钟。`Blackboard.append` 内部用 `store.now()` 生成 `created_at`，
#: 不接受外部传入——所以要控制时间就得从这个入口改。
_CLOCK = {"t": 0}


@pytest.fixture(autouse=True)
def _freeze_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    import hos_ares_core.blackboard as bbmod

    monkeypatch.setattr(bbmod, "now", lambda: _CLOCK["t"])


def _add(bb: Blackboard, *, value: str, origin: str, at: int, ev: str = "ev",
         subject: str = "svc", predicate: str = "version") -> Fact:
    _CLOCK["t"] = at
    return bb.append(task_id="t", subject=subject, predicate=predicate,
                     value=value, origin=origin, evidence_id=ev)


# ── 1：两个值不合并 ────────────────────────────────────────
def test_two_values_are_not_merged() -> None:
    bb, store = _bb()
    try:
        _add(bb, value="1.0", origin="inference", at=10)
        _add(bb, value="2.0", origin="observed", at=11)
        disputed = bb.disputed("t")
        members = disputed[("svc", "version")]
        assert len(members) == 2, [f.value for f in members]
        assert {f.value for f in members} == {"1.0", "2.0"}
    finally:
        store.close()


# ── 2：来源等级 observed > inference ───────────────────────
def test_observed_beats_inference() -> None:
    bb, store = _bb()
    try:
        _add(bb, value="inferred", origin="inference", at=99)   # 更新
        _add(bb, value="seen", origin="observed", at=10)        # 更旧，但来源更高
        res = bb.resolve("t")
        assert len(res) == 1, res
        assert res[0].winner.value == "seen", res[0].winner.value
        assert "observed" in res[0].reason, res[0].reason
    finally:
        store.close()


def test_origin_rank_table_is_the_source_of_truth() -> None:
    """排序必须用 `ORIGIN_RANK`，不是散在各处的字面量。"""
    assert ORIGIN_RANK["observed"] > ORIGIN_RANK["inference"], ORIGIN_RANK


# ── 3：同来源 → 新的胜 ──────────────────────────────────────
def test_same_origin_newer_wins() -> None:
    bb, store = _bb()
    try:
        _add(bb, value="old", origin="observed", at=10)
        _add(bb, value="new", origin="observed", at=20)
        res = bb.resolve("t")
        assert res[0].winner.value == "new", res[0].winner.value
    finally:
        store.close()


# ── 4：同毫秒 → 落库顺序，不按 ULID ────────────────────────
def test_same_millisecond_uses_insertion_order_not_id() -> None:
    """同毫秒的两条：后写的那条胜。

    按 ULID 字典序排会让结果随机的 4 位后缀漂移——用用例钉住"按 rowid"。
    """
    bb, store = _bb()
    try:
        first = _add(bb, value="first", origin="observed", at=50)
        second = _add(bb, value="second", origin="observed", at=50)
        res = bb.resolve("t")
        assert res[0].winner.value == "second", (
            f"同毫秒没有按落库顺序：winner={res[0].winner.value} "
            f"（first={first.id[:8]} second={second.id[:8]}）"
        )
    finally:
        store.close()


# ── 5：落选事实不删 ────────────────────────────────────────
def test_losers_are_not_deleted() -> None:
    """**落选的东西不许删**：审计要能看"当时以为是什么"。"""
    bb, store = _bb()
    try:
        _add(bb, value="discarded", origin="inference", at=10)
        _add(bb, value="kept", origin="observed", at=11)
        res = bb.resolve("t")
        assert [f.value for f in res[0].losers] == ["discarded"], res[0].losers
        # 失败方仍在库里
        rows = store.conn.execute(
            "SELECT value FROM blackboard_facts WHERE task_id='t'"
        ).fetchall()
        assert {r["value"] for r in rows} == {"discarded", "kept"}, (
            f"落选事实被删了：{[r['value'] for r in rows]}"
        )
    finally:
        store.close()


def test_injectable_excludes_losers_but_keeps_them_queryable() -> None:
    """注入视图里只有胜者；但账上两个都在。"""
    bb, store = _bb()
    try:
        _add(bb, value="discarded", origin="inference", at=10)
        _add(bb, value="kept", origin="observed", at=11)
        injected = [f.value for f in bb.injectable("t")]
        assert injected == ["kept"], injected
        assert len(bb.disputed("t")[("svc", "version")]) == 2
    finally:
        store.close()


# ── 6：冲突进报告 ─────────────────────────────────────────
def test_conflict_shows_up_in_the_report() -> None:
    bb, store = _bb()
    try:
        _add(bb, value="a", origin="inference", at=10)
        _add(bb, value="b", origin="observed", at=11)
        report = bb.conflict_report("t")
        assert report, "冲突没进报告"
        blob = str(report)
        assert "svc" in blob and "version" in blob, blob
        # 报告里要能看到"落选的那个是什么"——否则审计读不出当时的分歧
        assert "a" in blob, f"报告里看不到落选值：{blob}"
    finally:
        store.close()


# ── 7：量具能红 ───────────────────────────────────────────
def test_probe_can_observe_a_wrong_winner() -> None:
    """**先证明量具抓得到**：把 `ORIGIN_RANK` 反过来，`observed` 就该输。

    若反过来之后结论不变，说明那几条用例对真实现**没有区分力**。
    """
    import hos_ares_core.blackboard as bbmod

    original = dict(bbmod.ORIGIN_RANK)
    bb, store = _bb()
    try:
        bbmod.ORIGIN_RANK.clear()
        bbmod.ORIGIN_RANK.update({"observed": 1, "inference": 9})   # 故意反过来
        _add(bb, value="inferred", origin="inference", at=10)
        _add(bb, value="seen", origin="observed", at=11)
        res = bb.resolve("t")
        assert res[0].winner.value == "inferred", (
            f"把等级反过来后赢家没变：{res[0].winner.value} —— 这条量具没有区分力"
        )
    finally:
        bbmod.ORIGIN_RANK.clear()
        bbmod.ORIGIN_RANK.update(original)
        store.close()
