"""B13 死信测试（03 状态机 / 04 §7）。

四条纪律：
- 进死信**只有两条路**（重试耗尽 / terminal 类错误）；
- 必须带 error_code；
- 死信**不计入 error 率**；
- 重新投递**只能由人**。
"""

from __future__ import annotations

import pytest

from veripen_core.deadletter import (
    DEAD_CLASSES,
    DeadLetterError,
    column,
    redeliver,
    render_section,
    should_dead_letter,
    stats,
    to_dead_letter,
)
from veripen_core.models import TaskState
from veripen_core.store import Store

TASK = "t-1"


@pytest.fixture()
def store() -> Store:
    return Store(":memory:").init()


def _failed_task(store: Store, *, attempt: int = 3, max_attempts: int = 3) -> str:
    tid, _ = store.submit_task(
        engagement_id="e", profile_name="p", profile_hash="h", asset_id="a",
        oracle_ids=["o"], max_attempts=max_attempts,
    )
    store.conn.execute(
        "UPDATE tasks SET state=?, attempt=? WHERE id=?",
        (TaskState.FAILED.value, attempt, tid),
    )
    store.conn.commit()
    return tid


def _queued_task(store: Store, *, max_attempts: int = 3) -> str:
    tid, _ = store.submit_task(
        engagement_id="e2", profile_name="p", profile_hash="h", asset_id="a2",
        oracle_ids=["o"], max_attempts=max_attempts,
    )
    return tid


# ══════════════════════════════════════════════════════════════════
# 1 判定：只有两条路
# ══════════════════════════════════════════════════════════════════
def test_attempts_exhausted_qualifies() -> None:
    ok, why = should_dead_letter(attempt=3, max_attempts=3, error_class="retryable")
    assert ok is True and why == "attempts_exhausted"


def test_terminal_error_qualifies_immediately() -> None:
    ok, why = should_dead_letter(attempt=0, max_attempts=3, error_class="terminal")
    assert ok is True and why == "terminal_error"


def test_needs_human_qualifies() -> None:
    ok, _ = should_dead_letter(attempt=0, max_attempts=3, error_class="needs_human")
    assert ok is True


def test_retryable_below_cap_does_not_qualify() -> None:
    """还能重试就不许跳死信——「跳死信」是掩盖重试逻辑缺陷的最短路径。"""
    ok, why = should_dead_letter(attempt=1, max_attempts=3, error_class="retryable")
    assert ok is False and why == ""


def test_dead_classes_are_the_documented_two() -> None:
    assert DEAD_CLASSES == {"terminal", "needs_human"}


# ══════════════════════════════════════════════════════════════════
# 2 迁移：只能从 FAILED
# ══════════════════════════════════════════════════════════════════
def test_moves_from_failed(store: Store) -> None:
    tid = _failed_task(store)
    d = to_dead_letter(store, tid, error_class="retryable", error_code="VP-7001")
    assert d.task_id == tid
    assert d.exhausted is True
    assert store.get_task(tid)["state"] == TaskState.DEAD_LETTER.value


def test_rejects_non_failed_state(store: Store) -> None:
    """状态机图上只有 FAILED → DEAD_LETTER 这一条边。"""
    tid = _queued_task(store)
    with pytest.raises(DeadLetterError, match="只有 FAILED 可以进死信"):
        to_dead_letter(store, tid, error_class="terminal", error_code="X")


def test_rejects_unknown_task(store: Store) -> None:
    with pytest.raises(DeadLetterError, match="不存在"):
        to_dead_letter(store, "nope", error_class="terminal", error_code="X")


def test_rejects_when_cap_not_reached(store: Store) -> None:
    tid = _failed_task(store, attempt=1, max_attempts=3)
    with pytest.raises(DeadLetterError, match="还不到进死信的时候"):
        to_dead_letter(store, tid, error_class="retryable", error_code="X")


def test_requires_error_code(store: Store) -> None:
    tid = _failed_task(store)
    with pytest.raises(DeadLetterError, match="error_code"):
        to_dead_letter(store, tid, error_class="retryable", error_code="  ")


def test_rejects_double_dead_letter(store: Store) -> None:
    tid = _failed_task(store)
    to_dead_letter(store, tid, error_class="retryable", error_code="X")
    with pytest.raises(DeadLetterError, match="已在死信里"):
        to_dead_letter(store, tid, error_class="retryable", error_code="X")


# ══════════════════════════════════════════════════════════════════
# 3 单列统计：不计入 error
# ══════════════════════════════════════════════════════════════════
def test_stats_separate_exhausted_from_terminal(store: Store) -> None:
    t1 = _failed_task(store)
    to_dead_letter(store, t1, error_class="retryable", error_code="A")
    t2 = _queued_task(store)
    store.conn.execute("UPDATE tasks SET state=?, attempt=0 WHERE id=?",
                       (TaskState.FAILED.value, t2))
    store.conn.commit()
    to_dead_letter(store, t2, error_class="terminal", error_code="B")

    s = stats(store)
    assert s["dead_total"] == 2
    assert s["attempts_exhausted"] == 1
    assert s["terminal"] == 1


def test_dead_letters_are_not_errors(store: Store) -> None:
    """死信不是「这次没跑成」，是「重试用完了」——混进 error 率会让指标随重试策略跳动。"""
    assert stats(store)["counts_as_error"] is False


def test_column_is_empty_on_clean_store(store: Store) -> None:
    assert column(store) == []


def test_column_order_is_stable(store: Store) -> None:
    for i in range(3):
        # 三个不同资产：submit_task 有 idem_key 唯一约束，同参数会复用同一条任务，
        # 那样测出来的"三条"其实是同一个任务的三次迁移。
        tid, _ = store.submit_task(
            engagement_id="e3", profile_name="p", profile_hash="h",
            asset_id=f"a{i}", oracle_ids=["o"],
        )
        store.conn.execute("UPDATE tasks SET state=?, attempt=3 WHERE id=?",
                           (TaskState.FAILED.value, tid))
        store.conn.commit()
        to_dead_letter(store, tid, error_class="retryable", error_code=f"E{i}")
    got = [d.task_id for d in column(store)]
    assert len(got) == 3
    assert sorted(got) == sorted(set(got))


# ══════════════════════════════════════════════════════════════════
# 4 重新投递只能由人
# ══════════════════════════════════════════════════════════════════
def test_redeliver_requires_operator_name(store: Store) -> None:
    tid = _failed_task(store)
    to_dead_letter(store, tid, error_class="retryable", error_code="X")
    with pytest.raises(DeadLetterError, match="谁操作"):
        redeliver(store, tid, by="  ")


def test_redeliver_resets_attempt(store: Store) -> None:
    tid = _failed_task(store, attempt=3, max_attempts=3)
    to_dead_letter(store, tid, error_class="retryable", error_code="X")
    redeliver(store, tid, by="secops_lead")
    row = store.get_task(tid)
    assert row["state"] == TaskState.QUEUED.value
    assert int(row["attempt"]) == 0
    assert row["error_class"] is None


def test_redeliver_records_an_event(store: Store) -> None:
    tid = _failed_task(store)
    to_dead_letter(store, tid, error_class="retryable", error_code="X")
    redeliver(store, tid, by="secops_lead")
    rows = store.conn.execute(
        "SELECT detail_json FROM task_events WHERE task_id=?", (tid,)
    ).fetchall()
    assert any("redelivered" in str(r["detail_json"]) for r in rows)


def test_redeliver_rejects_task_not_in_dead_letter(store: Store) -> None:
    tid = _queued_task(store)
    with pytest.raises(DeadLetterError, match="不在死信里"):
        redeliver(store, tid, by="x")


# ══════════════════════════════════════════════════════════════════
# 5 报告章节
# ══════════════════════════════════════════════════════════════════
def test_section_says_none_when_empty(store: Store) -> None:
    text = render_section(store)
    assert text.startswith("## 死信（单列）")
    assert "无。" in text


def test_section_lists_each_dead_letter(store: Store) -> None:
    tid = _failed_task(store)
    to_dead_letter(store, tid, error_class="retryable", error_code="VP-7001")
    text = render_section(store)
    assert tid[:12] in text
    assert "3/3" in text
    assert "VP-7001" in text


def test_section_states_the_accounting_rule(store: Store) -> None:
    """口径要写在章节里，不能只写在代码注释里。"""
    assert "不计入 error 率" in render_section(store)
