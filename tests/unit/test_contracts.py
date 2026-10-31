"""B1 契约测试（01 §7 六条 + 幂等契约）。

这些测试比功能测试更重要：它们保证"架构没走样"。
同一份文件在 B1.5 之后仍然要全绿（后三条对 routes 层是前向兼容的）。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from veripen_core import Capsule, Verdict
from veripen_core.models import (
    Judgement,
    Oracle,
    RequestPair,
    canonical_json,
    seal_capsule,
)
from veripen_core.store import Store

ROOT = Path(__file__).resolve().parents[2]
CORE_DIR = ROOT / "veripen_core"
ORACLE_DIR = ROOT / "veripen_oracles"

#: 唯一允许构造 RouteBinding 的模块（27 N1）。B1 阶段该文件尚不存在 → 集合为空。
RESOLVER = "veripen_core/routes/resolver.py"

BANNED_SYMBOLS = {
    "fallback_path",
    "default_route",
    "retry_with_literal",
    "DEFAULT_ENDPOINT",
    "DEFAULT_PATH",
}


def _py_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _read_all_source(*roots: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for root in roots for p in _py_files(root))


def _builders_of(symbol: str, *roots: Path) -> set[str]:
    """AST 扫描：``symbol(...)`` 调用点所在文件（相对仓库根）。"""
    found: set[str] = set()
    for root in roots:
        for path in _py_files(root):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                func = getattr(node, "func", None)
                if func is None:
                    continue
                name = getattr(func, "id", None) or getattr(func, "attr", None)
                if name == symbol:
                    found.add(path.relative_to(ROOT).as_posix())
    return found


def _symbols_in(*roots: Path) -> set[str]:
    names: set[str] = set()
    for root in roots:
        for path in _py_files(root):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Name):
                    names.add(node.id)
                elif isinstance(node, ast.Attribute):
                    names.add(node.attr)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.add(node.name)
                elif isinstance(node, ast.arg):
                    names.add(node.arg)
    return names


class _PureOracle:
    """最小实现：judge 只依赖入参，无 IO / 时钟 / 随机。"""

    id = "contract_probe"
    version = "1.0.0"
    cwe = "CWE-0"
    requires: list[str] = []
    selector_tags: list[str] = ["object_read"]

    def applicable(self, ctx: object) -> bool:
        return True

    def plan(self, ctx: object, base: object) -> list:
        return []

    def judge(self, pairs: list[RequestPair]) -> Judgement:
        changed = any(p.baseline.body_sha256 != p.probe.body_sha256 for p in pairs)
        return Judgement(
            verdict=Verdict.VERIFIED if changed else Verdict.REFUTED,
            reason="differential_body_changed" if changed else "no_difference",
            assertion={"kind": "differential", "changed": changed},
            confidence=0.9,
            route_strength="observed",
        )


def _pair(body_a: str, body_b: str) -> RequestPair:
    from veripen_core.models import HttpTrace

    base = HttpTrace(route_ref="route://contract/opaque0000000001", status=200, body_sha256=body_a)
    probe = HttpTrace(route_ref="route://contract/opaque0000000001", status=200, body_sha256=body_b)
    return RequestPair(variant="bool_true", baseline=base, probe=probe)


# ── 1 依赖倒置 ──────────────────────────────────────────────────────


def test_core_does_not_import_oracles() -> None:
    assert "veripen_oracles" not in _read_all_source(CORE_DIR)


# ── 2 判定是纯函数 ──────────────────────────────────────────────────


def test_judge_is_pure() -> None:
    params = set(inspect.signature(Oracle.judge).parameters)
    assert params == {"self", "pairs"}, "judge 不得接收 ctx/时钟/网络句柄"

    oracle = _PureOracle()
    pairs = [_pair("aaa", "bbb")]
    results = {oracle.judge(pairs).verdict for _ in range(100)}
    assert results == {Verdict.VERIFIED}

    assertions = {canonical_json(oracle.judge(pairs).assertion) for _ in range(100)}
    assert len(assertions) == 1, "同输入必须产出同 assertion"


# ── 3 capsule 内容寻址 ──────────────────────────────────────────────


def _capsule() -> Capsule:
    return Capsule(
        finding_id="f-1",
        oracle_id="contract_probe",
        oracle_version="1.0.0",
        verdict=Verdict.VERIFIED,
        assertion={"kind": "differential", "changed": True},
        routeset_sha256="0" * 64,
        route_ref="route://contract/opaque0000000001",
        sealed_at=1,
    )


def test_capsule_is_content_addressed() -> None:
    assert seal_capsule(_capsule()).self_sha256 == seal_capsule(_capsule()).self_sha256

    tampered = _capsule().model_copy(update={"assertion": {"kind": "differential", "changed": False}})
    assert seal_capsule(tampered).self_sha256 != seal_capsule(_capsule()).self_sha256


# ── 4 可构造路径文本的类型不存在 ────────────────────────────────────


def test_routetext_not_importable() -> None:
    with pytest.raises(ImportError):
        from veripen_core import RouteText  # type: ignore[attr-defined]  # noqa: F401


# ── 5 resolver 是唯一构造点 ─────────────────────────────────────────


def test_resolver_is_only_builder() -> None:
    builders = _builders_of("RouteBinding", CORE_DIR, ORACLE_DIR)
    assert builders <= {RESOLVER}, f"RouteBinding 只许在 {RESOLVER} 构造，实际: {sorted(builders)}"


# ── 6 禁止"失败后换字面量路径" ──────────────────────────────────────


def test_no_retry_literal_path() -> None:
    offenders = _symbols_in(CORE_DIR, ORACLE_DIR) & BANNED_SYMBOLS
    assert not offenders, f"出现硬路由兜底符号: {sorted(offenders)}"


# ── 7 幂等契约（01 §6：同 idem_key 同 id）────────────────────────────


def test_task_idempotent_by_idem_key() -> None:
    store = Store(":memory:").init()
    try:
        first, reused_1 = store.submit_task(
            engagement_id="ENG-1",
            profile_name="demo-a",
            profile_hash="h1",
            asset_id="web-01",
            oracle_ids=["idor_cross_account", "sqli_bool_diff"],
            params={"depth": 1},
        )
        second, reused_2 = store.submit_task(
            engagement_id="ENG-1",
            profile_name="demo-a",
            profile_hash="h1",
            asset_id="web-01",
            oracle_ids=["sqli_bool_diff", "idor_cross_account"],
            params={"depth": 1},
        )
        assert first == second
        assert (reused_1, reused_2) == (False, True)

        third, _ = store.submit_task(
            engagement_id="ENG-1",
            profile_name="demo-a",
            profile_hash="h1",
            asset_id="web-02",
            oracle_ids=["idor_cross_account"],
        )
        assert third != first
    finally:
        store.close()


# ── 8 状态迁移防双跑（03 §2）────────────────────────────────────────


def test_transition_rejects_double_claim() -> None:
    store = Store(":memory:").init()
    try:
        task_id, _ = store.submit_task(
            engagement_id="ENG-1",
            profile_name="demo-a",
            profile_hash="h1",
            asset_id="web-01",
            oracle_ids=["idor_cross_account"],
        )
        from veripen_core.models import TaskState

        assert store.transition(task_id, TaskState.QUEUED, TaskState.LEASED) is True
        assert store.transition(task_id, TaskState.QUEUED, TaskState.LEASED) is False
    finally:
        store.close()
