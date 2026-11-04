"""B62 · L1a 跨任务记忆注入（H11 判据 A–E）。

核心不是"能不能压"，而是"压了之后判定有没有被污染"：
L1a 只影响先跑哪个，不产生 verdict。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from veripen_core.blackboard import AssetFacts
from veripen_core.briefing import L1A_MAX, L1A_PREFIX, build_l1a
from veripen_core.compression import verdict_fingerprint
from veripen_core.store import Store

REPO = Path(__file__).resolve().parents[2]


def _store() -> Store:
    return Store(":memory:").init()


def _seed(store: Store, *, asset_id: str = "api.test", n: int = 12,
          capsule: str = "cap-1", sealed: int = 1) -> AssetFacts:
    """造 N 条带 capsule 的事实；默认超过 L1A_MAX 以验证截断。"""
    store.conn.execute(
        "INSERT INTO capsules (id, finding_id, path, self_sha256, sealed_at)"
        " VALUES (?,?,?,?,?)",
        (capsule, "f-1", "/tmp/c.json", "0" * 64, sealed),
    )
    store.conn.commit()
    facts = AssetFacts(store)
    for i in range(n):
        facts.record_from_capsule(
            asset_id=asset_id,
            subject="route" if i % 2 == 0 else "header",
            predicate=f"p{i % 3}",
            value=f"v{i}",
            capsule_id=capsule,
            evidence_id=f"ev-{i}",
            origin="tool",
        )
    return facts


# ── A：injectable() 出现在生产路径 ──────────────────────────────
def test_injectable_has_production_caller() -> None:
    briefing = (REPO / "veripen_core" / "briefing.py").read_text(encoding="utf-8")
    hunt = (REPO / "veripen_cli" / "cmd_hunt.py").read_text(encoding="utf-8")
    assert "injectable(" in briefing, "briefing.py 必须调用 injectable()"
    assert "build_l1a(" in hunt, "cmd_hunt 必须调用 build_l1a()——否则仍是死代码"


# ── B：降幅 ────────────────────────────────────────────────────
def test_reduction_at_least_30_percent() -> None:
    store = _store()
    facts = _seed(store, n=30)
    l1a = build_l1a(facts, "api.test")
    assert l1a.compressed is not None
    assert l1a.tokens_before > 0
    assert l1a.reduction >= 0.30, f"降幅 {l1a.reduction:.0%} < 30%（before={l1a.tokens_before} after={l1a.tokens_after}）"


# ── C：判定与 L1a 无关 ─────────────────────────────────────────
def test_verdict_fingerprint_unchanged() -> None:
    verdicts = {"sqli_bool_diff": "CONFIRMED", "xss_reflected": "REJECTED"}
    store = _store()
    facts = _seed(store, n=8)
    l1a = build_l1a(facts, "api.test", verdicts=verdicts)
    assert l1a.compressed is not None
    assert l1a.compressed.verdicts_fingerprint == verdict_fingerprint(verdicts)


# ── D：条数与出处 ──────────────────────────────────────────────
def test_limit_and_capsule_required() -> None:
    store = _store()
    facts = _seed(store, n=20)
    l1a = build_l1a(facts, "api.test")
    assert len(l1a.used) <= L1A_MAX
    assert all(f.capsule_id for f in l1a.used), "无 capsule 出处的不许注入"
    assert l1a.text.startswith(L1A_PREFIX)
    # 20 条候选全部参与压缩（先在合并，再截行）——不能在压缩前就丢
    assert l1a.compressed is not None
    assert sum(len(line.merged_from) for line in l1a.compressed.facts) == 20


def test_row_truncation_counted() -> None:
    """行数超过 L1A_MAX 时截断并计入 dropped（与"合并"不是一回事）。"""
    store = _store()
    store.conn.execute(
        "INSERT INTO capsules (id, finding_id, path, self_sha256, sealed_at) VALUES (?,?,?,?,?)",
        ("cap-1", "f-1", "/tmp/c.json", "0" * 64, 1),
    )
    store.conn.commit()
    facts = AssetFacts(store)
    for i in range(15):
        facts.record_from_capsule(
            asset_id="api.test", subject=f"s{i}", predicate="p", value="v",
            capsule_id="cap-1", evidence_id=f"ev-{i}", origin="tool",
        )
    l1a = build_l1a(facts, "api.test")
    assert l1a.compressed is not None
    assert len(l1a.compressed.facts) == 15, "15 个不同 subject 合并不成行"
    assert l1a.dropped == 5, f"应截掉 5 行，实际 dropped={l1a.dropped}"


def test_empty_asset_returns_empty_text() -> None:
    store = _store()
    facts = AssetFacts(store)
    l1a = build_l1a(facts, "")
    assert l1a.text == ""
    assert l1a.used == ()


# ── E：过期标 stale 不删 ───────────────────────────────────────
def test_stale_counted_not_deleted(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store()
    facts = _seed(store, n=3)
    # 把 ttl 改成 0 并让 now 前进，制造"已过期"
    store.conn.execute("UPDATE asset_facts SET ttl_s=1, created_at=1")
    store.conn.commit()

    l1a = build_l1a(facts, "api.test")
    assert l1a.text == "", "全部过期时不应注入任何条目"
    assert len(l1a.stale_ids) == 3, "过期的要计数，不能悄悄消失"
    # 行还在库里
    left = store.conn.execute("SELECT COUNT(*) AS n FROM asset_facts").fetchone()["n"]
    assert left == 3, "过期不删"


# ── 组装 ───────────────────────────────────────────────────────
def test_with_goal_noop_when_empty() -> None:
    store = _store()
    l1a = build_l1a(AssetFacts(store), "api.test")
    assert l1a.with_goal("验证 api.test") == "验证 api.test"


def test_with_goal_appends_when_present() -> None:
    store = _store()
    facts = _seed(store, n=2)
    l1a = build_l1a(facts, "api.test")
    out = l1a.with_goal("验证 api.test")
    assert out.startswith("验证 api.test\n")
    assert L1A_PREFIX in out
