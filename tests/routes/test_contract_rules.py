"""路由层契约测试（01 §7 的 v3 三条 N1/N2/N4）+ 确定性断言。

``pytest -k routes_contract`` 必须 3 绿（21 §B1.5 DoD）。
"""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

import pytest

from veripen_core.routes import (
    AssetRef,
    Resolver,
    RouteHints,
    RouteProvenance,
    RouteQuery,
    StaticSource,
)
from veripen_core.routes.types import RouteCandidate

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "veripen_core"
ORACLES = ROOT / "veripen_oracles"
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
        from veripen_core.routes import RouteText  # type: ignore[attr-defined]  # noqa: F401


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
    assert builders == {"veripen_core/routes/resolver.py"}


def test_routes_contract_n4_no_retry_symbol() -> None:
    spec = importlib.util.spec_from_file_location("nr_contract", ROOT / "scripts/lint/noroute.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)

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
