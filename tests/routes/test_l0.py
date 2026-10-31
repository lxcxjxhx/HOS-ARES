"""L0 路由层测试（11 §2，L0-01…L0-35）。

**这层是 B1.5 的验收清单**：少于 35 条通过即未完成。
不联网、不需要模型、不需要靶场；模板全部来自 ``fixtures/templates.json``。

为什么模板不写在测试里：``tests/`` 在 ``lint-noroute`` 的扫描范围内，
而模板就是“录下来的路径”。夹具目录是唯一整目录豁免（与 docs/research 同源精神）。
"""

from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from veripen_core.routes import (
    AssetRef,
    ContractHostError,
    ContractLocatorUnresolved,
    DiscoveryReason,
    LocatorLevel,
    QuotaExceeded,
    RediscoveryBudget,
    Resolver,
    RouteHints,
    RouteProvenance,
    RouteQuery,
    RouteStale,
    StaticSource,
    assert_fresh,
    build_binding,
    compute_confidence,
    locate_contract,
    merge_by_structural_key,
    normalize_template,
    opaque16,
    resolve_routes,
    sort_key,
    structural_key,
    template_sha256,
    truncate_for_selector,
    validate_contract_hosts,
)
from veripen_core.routes.types import RouteCandidate

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
TEMPLATES = json.loads((FIXTURES / "templates.json").read_text(encoding="utf-8"))

CORE = ROOT / "veripen_core"
ORACLES = ROOT / "veripen_oracles"
RESOLVER_REL = "veripen_core/routes/resolver.py"


# ── 公共工具 ────────────────────────────────────────────────────────


def _load_tool(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def cand(
    key: str,
    *,
    method: str = "GET",
    source: RouteProvenance = RouteProvenance.CONTRACT,
    param_names: tuple[str, ...] = ("id",),
    auth: str | None = None,
    body: dict | None = None,
    verifications: int = 0,
    ambiguity: int = 0,
) -> RouteCandidate:
    from veripen_core.routes.sources.base import body_shape_sha256

    return RouteCandidate(
        method=method,
        template=TEMPLATES[key],
        param_names=param_names,
        auth_requirement=auth,
        body_shape_sha256=body_shape_sha256(body),
        source=source,
        verifications=verifications,
        param_ambiguity=ambiguity,
    )


def src(name: str, prov: RouteProvenance, items: list[RouteCandidate], err: str | None = None):
    return StaticSource(name=name, provenance=prov, candidates=items, raise_error=err)


def q(tag: str = "object_read", **kw) -> RouteQuery:
    return RouteQuery(asset=AssetRef(id="web-01"), hints=RouteHints(semantic_tag=tag), **kw)


def py_files(*roots: Path) -> list[Path]:
    out: list[Path] = []
    for root in roots:
        if root.exists():
            out += [
                p for p in root.rglob("*.py") if "__pycache__" not in p.parts
            ]
    return sorted(out)


def symbols_in(*roots: Path) -> set[str]:
    names: set[str] = set()
    for p in py_files(*roots):
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
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


# ── 2.1 类型与构造（N1/N2）──────────────────────────────────────────


def test_l0_01_routetext_not_in_routes() -> None:
    with pytest.raises(ImportError):
        from veripen_core.routes import RouteText  # type: ignore[attr-defined]  # noqa: F401


def test_l0_02_routetext_not_in_core() -> None:
    with pytest.raises(ImportError):
        from veripen_core import RouteText  # type: ignore[attr-defined]  # noqa: F401


def test_l0_03_resolver_is_only_builder() -> None:
    builders: set[str] = set()
    for p in py_files(CORE, ORACLES):
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        for node in ast.walk(tree):
            func = getattr(node, "func", None)
            if func is None:
                continue
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name == "RouteBinding":
                builders.add(p.relative_to(ROOT).as_posix())
    assert builders == {RESOLVER_REL}


def test_l0_04_send_rejects_string() -> None:
    from veripen_core.tools.http import send

    with pytest.raises(TypeError):
        send("not-a-binding", {}, None)  # type: ignore[arg-type]


def test_l0_05_no_route_fallback_symbols() -> None:
    noroute = _load_tool("nr_rules", "scripts/lint/noroute.py")
    offenders = symbols_in(CORE, ORACLES) & set(noroute.BANNED_SYMBOLS)
    assert not offenders, f"出现兜底符号: {sorted(offenders)}"


def test_l0_06_mypy_strict_clean() -> None:
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "mypy", "--strict", "--no-error-summary", str(CORE)],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
    except FileNotFoundError:  # pragma: no cover
        pytest.fail("mypy 不可用：无法验证 ≠ 通过")
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ── 2.2 合并与 structural_key ───────────────────────────────────────


def test_l0_07_contract_plus_observed_merges() -> None:
    merged = merge_by_structural_key([cand("user_by_id"), cand("user_by_id", source=RouteProvenance.OBSERVED)])
    assert len(merged) == 1
    assert merged[0].sources_all == ("contract", "observed")
    assert merged[0].confidence == pytest.approx(0.90 + 0.05)


def test_l0_08_auth_requirement_prevents_merge() -> None:
    merged = merge_by_structural_key(
        [cand("user_by_id", auth="primary"), cand("user_by_id", auth="admin")]
    )
    assert len(merged) == 2


def test_l0_09_body_shape_prevents_merge() -> None:
    merged = merge_by_structural_key(
        [cand("items", body={"a": "int"}), cand("items", body={"b": "str"})]
    )
    assert len(merged) == 2


def test_l0_10_param_order_does_not_split() -> None:
    merged = merge_by_structural_key(
        [cand("pair_ab", param_names=("a", "b")), cand("pair_ba", param_names=("b", "a"))]
    )
    assert len(merged) == 1


def test_l0_11_trailing_slash_normalized() -> None:
    assert normalize_template(TEMPLATES["user_by_id_slash"]) == normalize_template(
        TEMPLATES["user_by_id"]
    )
    merged = merge_by_structural_key([cand("user_by_id"), cand("user_by_id_slash")])
    assert len(merged) == 1


def test_l0_12_path_case_distinguishes() -> None:
    merged = merge_by_structural_key([cand("user_by_id"), cand("user_by_id_upper")])
    assert len(merged) == 2


def test_l0_13_ref_derived_from_structural_key() -> None:
    first = Resolver(sources=[src("contract", RouteProvenance.CONTRACT, [cand("user_by_id")])]).resolve(q(), now=1)
    second = Resolver(sources=[src("contract", RouteProvenance.CONTRACT, [cand("user_by_id")])]).resolve(q(), now=999)
    assert first.bindings[0].ref == second.bindings[0].ref
    key = structural_key("GET", TEMPLATES["user_by_id"], ("id",), None, "nobody")
    assert first.bindings[0].ref.endswith(opaque16(key))


# ── 2.3 置信度 ──────────────────────────────────────────────────────


def test_l0_14_probe_only_is_unusable() -> None:
    rs = Resolver(sources=[src("probe", RouteProvenance.PROBE, [cand("user_by_id", source=RouteProvenance.PROBE)])]).resolve(q(), now=1)
    b = rs.bindings[0]
    assert b.confidence == pytest.approx(0.50)
    assert b.confidence < q().min_confidence
    assert b.usable is False


def test_l0_15_contract_only_is_ninety() -> None:
    rs = Resolver(sources=[src("contract", RouteProvenance.CONTRACT, [cand("user_by_id")])]).resolve(q(), now=1)
    assert rs.bindings[0].confidence == pytest.approx(0.90)


def test_l0_16_corroboration_adds_and_clamps() -> None:
    rs = Resolver(
        sources=[
            src("contract", RouteProvenance.CONTRACT, [cand("user_by_id")]),
            src("observed", RouteProvenance.OBSERVED, [cand("user_by_id", source=RouteProvenance.OBSERVED, verifications=2)]),
        ]
    ).resolve(q(), now=1)
    assert rs.bindings[0].confidence == pytest.approx(1.0)


def test_l0_17_param_ambiguity_penalty() -> None:
    base = compute_confidence(("contract",), 0, 0)
    penalized = compute_confidence(("contract",), 0, 2)
    assert base - penalized == pytest.approx(0.20)
    assert penalized == pytest.approx(0.70)


def test_l0_18_confidence_never_assigned_literal() -> None:
    offenders: list[str] = []
    for p in py_files(CORE / "routes"):
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if not isinstance(value, ast.Constant) or not isinstance(value.value, float):
                continue
            if value.value == 0.0:
                # 0.0 是 dataclass 字段的“未设置”占位；有意义的置信度必须由函数算出
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                name = getattr(t, "id", None) or getattr(t, "attr", None) or ""
                # 只抓“把置信度写死”的赋值；min_confidence 之类是阈值配置，不是判定置信度
                if name == "confidence":
                    offenders.append(f"{p.name}:{node.lineno}")
    assert not offenders, f"confidence 字面赋值: {offenders}"


# ── 2.4 裁决与截断 ──────────────────────────────────────────────────


def test_l0_19_zero_hits_records_unresolved() -> None:
    kept, unresolved = truncate_for_selector([], "object_read")
    assert kept == []
    assert unresolved is not None and unresolved.reason == "no_candidate"


def test_l0_20_seven_hits_truncated_to_five() -> None:
    cands = [cand(f"probe_r{i}") for i in range(7)]
    rs = Resolver(sources=[src("contract", RouteProvenance.CONTRACT, cands)]).resolve(q(), now=1)
    kept, unresolved = truncate_for_selector(rs.bindings, "object_read")
    assert len(kept) == 5
    assert unresolved is not None
    assert unresolved.reason == "truncated"
    assert unresolved.detail["truncated"] == 2


def test_l0_21_three_hits_all_returned_sorted() -> None:
    cands = [cand(k) for k in ("three_a", "three_b", "three_c")]
    rs = Resolver(sources=[src("contract", RouteProvenance.CONTRACT, cands)]).resolve(q(), now=1)
    kept, unresolved = truncate_for_selector(rs.bindings, "object_read")
    assert unresolved is None
    assert len(kept) == 3
    assert [b.template_sha256 for b in kept] == [b.template_sha256 for b in sorted(kept, key=sort_key)]


# ── 2.5 确定性 ──────────────────────────────────────────────────────


def test_l0_22_source_order_does_not_change_sha() -> None:
    a = cand("user_by_id")
    b = cand("admin")
    forward = Resolver(sources=[src("contract", RouteProvenance.CONTRACT, [a, b])]).resolve(q(), now=5)
    reverse = Resolver(sources=[src("contract", RouteProvenance.CONTRACT, [b, a])]).resolve(q(), now=5)
    assert forward.routeset_sha256 == reverse.routeset_sha256


def test_l0_23_concurrency_does_not_change_sha() -> None:
    results: list[str] = []
    lock = threading.Lock()

    def run() -> None:
        rs = Resolver(sources=[src("contract", RouteProvenance.CONTRACT, [cand("user_by_id")])]).resolve(q(), now=7)
        with lock:
            results.append(rs.routeset_sha256)

    threads = [threading.Thread(target=run) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(results)) == 1


def test_l0_24_recorded_fixture_replays() -> None:
    recorded = json.loads((FIXTURES / "recorded_routeset.json").read_text(encoding="utf-8"))
    cands = [
        RouteCandidate(
            method=c["method"],
            template=TEMPLATES[c["template_key"]],
            param_names=tuple(c["param_names"]),
            auth_requirement=c.get("auth"),
            body_shape_sha256=c.get("body_shape_sha256", "nobody"),
            source=RouteProvenance(c["source"]),
            verifications=c.get("verifications", 0),
        )
        for c in recorded["candidates"]
    ]
    rs = resolve_routes(q(), now=recorded["now"], sources=[src("recorded", RouteProvenance.CONTRACT, cands)])
    assert rs.routeset_sha256 == recorded["recorded_routeset_sha256"]


def test_l0_25_equal_scores_ordered_by_template_sha() -> None:
    items = [cand("order_x"), cand("order_y")]
    one = Resolver(sources=[src("contract", RouteProvenance.CONTRACT, items)]).resolve(q(), now=1)
    two = Resolver(sources=[src("contract", RouteProvenance.CONTRACT, list(reversed(items)))]).resolve(q(), now=1)
    hashes_one = [b.template_sha256 for b in one.bindings]
    hashes_two = [b.template_sha256 for b in two.bindings]
    assert hashes_one == hashes_two == sorted(hashes_one)
    assert template_sha256(TEMPLATES["order_x"]) in hashes_one


# ── 2.6 失效与重发现 ────────────────────────────────────────────────


def test_l0_26_ttl_expired_never_resends_old_binding() -> None:
    rs = Resolver(sources=[src("contract", RouteProvenance.CONTRACT, [cand("user_by_id")])]).resolve(
        q(ttl_s=1), now=100
    )
    assert_fresh(rs, now=100)

    sends: list[str] = []

    def attempt() -> None:
        assert_fresh(rs, now=200)
        sends.append(rs.bindings[0].ref)

    with pytest.raises(RouteStale):
        attempt()
    assert sends == [], "过期绑定一次都不许重发"
    assert rs.expired(200) is True


def test_l0_27_rediscovery_quota() -> None:
    budget = RediscoveryBudget(max_rediscovery=1)
    budget.spend()
    with pytest.raises(QuotaExceeded):
        budget.spend()
    with pytest.raises(QuotaExceeded):
        budget.spend()
    assert budget.used == 1


def test_l0_28_one_source_failure_does_not_fail_all() -> None:
    rs = Resolver(
        sources=[
            src("contract", RouteProvenance.CONTRACT, [], err="契约取不到"),
            src("observed", RouteProvenance.OBSERVED, [cand("user_by_id", source=RouteProvenance.OBSERVED)]),
            src("operator", RouteProvenance.OPERATOR, []),
        ]
    ).resolve(q(), now=1)
    assert len(rs.bindings) == 1
    assert rs.sources_failed == ("contract",)
    assert "observed" in rs.sources_used


# ── 2.7 边界与自举 ──────────────────────────────────────────────────


def test_l0_29_locator_detects_cycle() -> None:
    same = ("opaque-cycle", ["doc"])
    levels = [
        LocatorLevel(name="observed", resolve=lambda: same),
        LocatorLevel(name="well_known", resolve=lambda: same),
    ]
    with pytest.raises(ContractLocatorUnresolved) as info:
        locate_contract(levels, parse_ok=lambda _doc: False)
    assert "环" in str(info.value) or "cycle" in str(info.value)


def test_l0_30_locator_stops_at_three_hops() -> None:
    levels = [
        LocatorLevel(name=f"l{i}", resolve=(lambda i=i: (f"ref-{i}", ["doc"]))) for i in range(4)
    ]
    with pytest.raises(ContractLocatorUnresolved):
        locate_contract(levels, parse_ok=lambda _doc: False, max_hops=3)


def test_l0_31_zero_operations_falls_through() -> None:
    levels = [
        LocatorLevel(name="observed", resolve=lambda: ("ref-empty", [])),
        LocatorLevel(name="operator", resolve=lambda: ("ref-good", ["op"])),
    ]
    result = locate_contract(levels, parse_ok=lambda doc: len(doc) > 0)
    assert result.ref == "ref-good"
    assert result.hops == 2


def test_l0_32_contract_host_outside_assets_rejected() -> None:
    validate_contract_hosts([TEMPLATES["host_good"]], [TEMPLATES["host_ok"]])
    with pytest.raises(ContractHostError) as info:
        validate_contract_hosts([TEMPLATES["host_bad"]], [TEMPLATES["host_ok"]])
    assert info.value.code == "VP-1006"


def test_l0_33_no_network_imports_in_adapters() -> None:
    netimports = _load_tool("ni_rules", "scripts/lint/netimports.py")
    paths = [CORE / "routes", ROOT / "veripen_contract"]
    assert netimports.scan(paths) == []


def test_l0_34_import_check_catches_violation(tmp_path: Path) -> None:
    netimports = _load_tool("ni_rules2", "scripts/lint/netimports.py")
    bad = tmp_path / "adapter.py"
    bad.write_text("import requests\n", encoding="utf-8")
    hits = netimports.scan([tmp_path])
    assert any("requests" in h for h in hits)


def test_l0_35_gate_really_reddens(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    bad = tmp_path / "tests" / "bad_case.py"
    # 故意拼出“看起来像地址”的坏样本；用 chr 构造，避免本测试自身触发 R-PATH
    slash = chr(0x2F)
    lines = [
        "A = " + '"' + "https" + ":" + slash * 2 + "h.invalid" + slash + 'p"' + "\n",
        "B = " + "'" + slash + "admin'" + "\n",
        "C = " + "'" + "port=" + "8080" + "'" + "\n",
    ]
    bad.write_text("".join(lines), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "lint" / "noroute.py"), "--root", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, proc.stdout
    assert "R-PATH" in proc.stdout and "R-URL" in proc.stdout and "R-PORT" in proc.stdout
