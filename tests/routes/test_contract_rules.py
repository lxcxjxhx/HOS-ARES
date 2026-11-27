"""路由层契约测试（01 §7 的 v3 三条 N1/N2/N4）+ 确定性断言。

``pytest -k routes_contract`` 必须 3 绿（21 §B1.5 DoD）。
"""

from __future__ import annotations

import sys
import ast
import importlib.util
import json
from pathlib import Path

import pytest

from hos_ares_core.routes import (
    AssetRef,
    Resolver,
    RouteHints,
    RouteProvenance,
    RouteQuery,
    StaticSource,
)
from hos_ares_core.routes.types import RouteCandidate
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
from script_module import load_script_module  # noqa: E402
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "hos_ares_core"
ORACLES = ROOT / "hos_ares_oracles"
TEMPLATES = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "templates.json").read_text(encoding="utf-8")
)


def _py_files(*roots: Path) -> list[Path]:
    out: list[Path] = []
    for root in roots:
        if root.exists():
            out += [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]
    return sorted(out)


def test_routes_contract_n1_routetext_hidden() -> None:
    with pytest.raises(ImportError):
        from hos_ares_core.routes import RouteText  # type: ignore[attr-defined]  # noqa: F401


def test_routes_contract_n2_resolver_only_builder() -> None:
    builders: set[str] = set()
    for p in _py_files(CORE, ORACLES):
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        for node in ast.walk(tree):
            func = getattr(node, "func", None)
            if func is None:
                continue
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name == "RouteBinding":
                builders.add(p.relative_to(ROOT).as_posix())
    assert builders == {"hos_ares_core/routes/resolver.py"}


def test_routes_contract_n4_no_retry_symbol() -> None:
    # 用共用函数（B588）。这处原来把 `assert` 写在 `module_from_spec` **之后**。
    mod: Any = load_script_module("nr_contract", ROOT / "scripts/lint/noroute.py")

    names: set[str] = set()
    for p in _py_files(CORE, ORACLES):
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        for node in ast.walk(tree):
            names |= {
                getattr(n, "id", "") or getattr(n, "attr", "")
                for n in ast.walk(node)
                if isinstance(n, (ast.Name, ast.Attribute))
            }
    assert not (names & set(mod.BANNED_SYMBOLS))


def test_routeset_same_query_same_sha() -> None:
    def build() -> str:
        cands = [
            RouteCandidate(
                method="GET",
                template=TEMPLATES["thing"],
                param_names=("id",),
                source=RouteProvenance.CONTRACT,
            )
        ]
        q = RouteQuery(asset=AssetRef(id="web-01"), hints=RouteHints(semantic_tag="object_read"))
        rs = Resolver(sources=[StaticSource("contract", RouteProvenance.CONTRACT, cands)]).resolve(q, now=42)
        return rs.routeset_sha256

    assert build() == build()
