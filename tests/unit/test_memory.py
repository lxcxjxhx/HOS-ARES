"""B57 跨任务记忆（`H04 §4` / `H07` B57）。

判据：出处强制 · 过期不注入但保留 · **结论不跨任务**（删记忆后同输入的判定结果不变）·
注入是事实语气 · 迁移向后兼容（旧库可读）。
"""

from __future__ import annotations

import pytest

from veripen_core.blackboard import Blackboard
from veripen_core.memory import DEFAULT_TTL_S, Memory, MemoryError
from veripen_core.store import Store


@pytest.fixture()
def store() -> Store:
    s = Store(":memory:").init()
    yield s
    s.close()


def _mem(store: Store) -> Memory:
    return Memory(Blackboard(store))


# ── A 出处强制 ─────────────────────────────────────────────────
def test_remember_requires_source_event_id(store: Store) -> None:
    with pytest.raises(MemoryError) as err:
        _mem(store).remember(asset="web-01", kind="stack", value="php-8.1",
                             source_event_id="  ")
    assert err.value.code == "missing_source"


def test_remember_rejects_empty_key(store: Store) -> None:
    with pytest.raises(MemoryError):
        _mem(store).remember(asset="", kind="stack", value="x", source_event_id="ev-1")


def test_remembered_fact_keeps_source_and_ttl(store: Store) -> None:
    mem = _mem(store)
    fact = mem.remember(asset="web-01", kind="stack", value="php-8.1",
                        source_event_id="ev-42", ttl_s=60)
    assert fact.evidence_id == "ev-42" and fact.ttl_s == 60
    assert fact.predicate.startswith("mem.")


# ── B 过期：不注入但保留 ───────────────────────────────────────
def test_expired_fact_is_not_recalled_but_kept(store: Store) -> None:
    mem = _mem(store)
    fact = mem.remember(asset="web-01", kind="stack", value="php-8.1",
                        source_event_id="ev-1", ttl_s=10)
    now = fact.created_at + 11
    assert mem.recall("web-01", now=now) == ()
    assert len(mem.stale("web-01", now=now)) == 1
    assert len(store.conn.execute("SELECT 1 FROM blackboard_facts").fetchall()) == 1, \
        "过期不等于删除（I10）"


def test_default_ttl_is_thirty_days(store: Store) -> None:
    mem = _mem(store)
    fact = mem.remember(asset="a", kind="k", value="v", source_event_id="ev-1")
    assert fact.ttl_s == DEFAULT_TTL_S and DEFAULT_TTL_S == 30 * 24 * 3600


def test_recall_is_scoped_by_asset(store: Store) -> None:
    mem = _mem(store)
    fa = mem.remember(asset="a", kind="k", value="1", source_event_id="ev-1")
    mem.remember(asset="b", kind="k", value="2", source_event_id="ev-2")
    now = fa.created_at + 1
    assert [e.value for e in mem.recall("a", now=now)] == ["1"]
    assert {e.value for e in mem.recall(now=now)} == {"1", "2"}


# ── C 结论不跨任务 ────────────────────────────────────────────
def test_memory_api_exposes_no_verdict(store: Store) -> None:
    """记忆层不许有返回 verdict 的路径——连字段都不该出现。"""
    import inspect

    from veripen_core import memory as module

    source = inspect.getsource(module)
    assert "VERIFIED" not in source and "judge(" not in source


def test_findings_table_untouched_by_memory(store: Store) -> None:
    before = store.conn.execute("SELECT count(*) AS c FROM findings").fetchone()["c"]
    mem = _mem(store)
    mem.remember(asset="web-01", kind="stack", value="php", source_event_id="ev-1")
    mem.recall("web-01", now=10 ** 12)
    after = store.conn.execute("SELECT count(*) AS c FROM findings").fetchone()["c"]
    assert before == after, "记忆不得产生或修改任何发现"


def test_prompt_lines_state_facts_with_source(store: Store) -> None:
    mem = _mem(store)
    fact = mem.remember(asset="web-01", kind="stack", value="php-8.1", source_event_id="ev-7")
    lines = mem.as_prompt_lines("web-01", now=fact.created_at + 1)
    assert lines == ("web-01.stack = php-8.1（出处 ev-7）",)
    assert "漏洞" not in lines[0] and "已确认" not in lines[0]


# ── D 旧库兼容 ────────────────────────────────────────────────
def test_plain_blackboard_facts_are_ignored(store: Store) -> None:
    """不是以 `mem.` 开头的谓词属于本轮观测，不该被当成跨任务记忆。"""
    Blackboard(store).append(task_id="memory", subject="web-01", predicate="server",
                             value="nginx", origin="observed", evidence_id="ev-1")
    assert _mem(store).recall("web-01", now=10 ** 10) == ()
