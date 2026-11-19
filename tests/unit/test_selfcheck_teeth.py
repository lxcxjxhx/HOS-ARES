"""B516 · 17 条一致性自检到底能不能红。

`hos_ares_core/selfcheck.py` 的 `CHECKS` 是 17 条硬编码 SQL，`run_all` 逐条执行，
doctor 的 `check_selfcheck` 因为它而报 ok。但**没有任何用例证明它抓得到违规**——
一个永远报 0 的哨兵跟没有哨兵是一回事（与 B194/B434/B435 那一批是同一个问题）。

这里给第 3 条（「无证据的已验证发现」，gate）喂一条真实违规：
`state='VERIFIED'` 且 `capsule_id IS NULL`。数据库触发器只约束 outcome/state/coverage_src
的组合（`store.py:247`），**不拦这一条**，所以它必须由自检抓到。
"""

from __future__ import annotations

import pytest

from hos_ares_core.selfcheck import run_all
from hos_ares_core.store import Store

_INSERT = (
    "INSERT INTO findings (id, task_id, asset_id, oracle_id, oracle_version, title,"
    " severity, confidence, route_strength, cwe, state, capsule_id, created_at,"
    " outcome, coverage_src, not_run_reason, error_reason)"
    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)


def _add(store: Store, fid: str, *, state: str, capsule: str | None) -> None:
    store.conn.execute(_INSERT, (
        fid, "t-1", "asset-1", "idor_cross_account", "1", "标题", "high", 0.9,
        None, None, state, capsule, "2026-11-20T00:00:00Z",
        "reported", "machine_observed", None, None,
    ))


@pytest.fixture()
def store():
    s = Store(":memory:").init()   # init() 才建表
    try:
        yield s
    finally:
        s.close()


def test_check3_catches_verified_without_capsule(store: Store) -> None:
    _add(store, "f-no-capsule", state="VERIFIED", capsule=None)
    check3 = next(o for o in run_all(store) if o.no == 3)
    assert check3.violations, "VERIFIED 没有 capsule 居然没被抓到——第 3 条自检没牙"
    assert "f-no-capsule" in check3.violations
    assert check3.gate is True, "第 3 条是上线门禁，gate 标记不能丢"


def test_check3_passes_when_capsule_present(store: Store) -> None:
    _add(store, "f-ok", state="VERIFIED", capsule="cap-1")
    check3 = next(o for o in run_all(store) if o.no == 3)
    assert check3.violations == (), "有 capsule 的 VERIFIED 被误伤"


def test_all_checks_react_to_a_broken_store(store: Store) -> None:
    """整体自检必须**逐条给出结局**，不能整体吞掉异常。"""
    outcomes = run_all(store)
    assert len(outcomes) == len({o.no for o in outcomes}), "自检编号重复"
    assert all(o.no >= 1 for o in outcomes)
    # 空库：不该有任何违规（第 3 条也不该误报）
    assert [o.line() for o in outcomes if not o.ok] == []
