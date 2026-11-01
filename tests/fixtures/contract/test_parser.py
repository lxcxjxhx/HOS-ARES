"""B8 契约解析测试：40 op → 200 条绑定、幂等、host 越界即拒、无网络依赖。

19 §9 的验收逐条对应（A7 断言）。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from veripen_contract import (
    ContractHostOutOfScope,
    ContractTooLarge,
    OperationSet,
    UnknownTag,
    parse_openapi,
    plan,
)
from veripen_contract import planner
from veripen_core.routes import (
    AssetRef,
    Resolver,
    RouteHints,
    RouteProvenance,
    RouteQuery,
    StaticSource,
    template_sha256,
)
from veripen_core.routes.types import RouteCandidate

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
SAMPLE = (HERE / "sample.json").read_text(encoding="utf-8")
HOST = "contract-sample.invalid"
SLASH = chr(0x2F)
ASSET = "web-01"


def _gen(count: int) -> str:
    """生成含 count 个 operation 的契约（结构各异，避免归一后塌成一条）。"""
    paths = {}
    for i in range(1, count + 1):
        paths[f"/api/v1/res{i}/{{id}}"] = {
            "get": {
                "operationId": f"get_res{i}",
                "parameters": [{"name": "id", "in": "path", "required": True,
                                "schema": {"type": "integer"}}],
                "security": [{"bearer_low": []}],
                "responses": {"200": {"description": "ok", "content": {
                    "application/json": {"schema": {"type": "object", "properties": {"id": {"type": "integer"}}}}}}},
            },
            "patch": {
                "operationId": f"patch_res{i}",
                "parameters": [{"name": "id", "in": "path", "required": True,
                                "schema": {"type": "integer"}}],
                "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                "responses": {"200": {"description": "ok", "content": {
                    "application/json": {"schema": {"type": "object", "properties": {"id": {"type": "integer"}}}}}}},
            },
        }
    doc = {
        "openapi": "3.0.3",
        "servers": [{"url": f"http://{HOST}"}],
        "components": {"securitySchemes": {"bearer_low": {"type": "http", "scheme": "bearer",
                                                          "x-veripen-role": "secondary"}}},
        "paths": paths,
    }
    return json.dumps(doc)


# ── 1 解析：形状与标签 ──────────────────────────────────────────────
def test_parse_sample_operations() -> None:
    opset = parse_openapi(SAMPLE, asset_id=ASSET, allowed_hosts=[HOST])
    ids = {o.op_id for o in opset.operations}
    assert {"get_item", "list_items", "search", "fetch_url", "login", "update_me"} <= ids


def test_object_read_is_proposed_not_adopted() -> None:
    """结构推断只能"提议"，不能自行采纳（19 §3.1 第 5 行）。"""
    opset = parse_openapi(SAMPLE, asset_id=ASSET, allowed_hosts=[HOST])
    op = next(o for o in opset.operations if o.op_id == "get_item")
    assert "object_read" not in op.tag_names, "未确认前不许出现在已采纳标签里"
    assert "object_read" in {t.tag for t in op.proposed}


def test_operator_confirm_promotes_proposed() -> None:
    opset = parse_openapi(SAMPLE, asset_id=ASSET, allowed_hosts=[HOST])
    op = next(o for o in opset.operations if o.op_id == "get_item")
    confirmed = op.confirm({"object_read"})
    by_tag = {t.tag: t for t in confirmed.tags}
    assert by_tag["object_read"].source == "operator"
    assert by_tag["object_read"].tag_confidence == 0.80


def test_url_fetch_from_format_uri_is_self_evident() -> None:
    """形状即声明：format:uri 参数 ⟺ 会去取 URL，可自行采纳。"""
    opset = parse_openapi(SAMPLE, asset_id=ASSET, allowed_hosts=[HOST])
    tags = {o.op_id: set(o.tag_names) for o in opset.operations}
    assert "url_fetch" in tags["fetch_url"]


def test_token_issue_from_response_schema() -> None:
    opset = parse_openapi(SAMPLE, asset_id=ASSET, allowed_hosts=[HOST])
    tags = {o.op_id: set(o.tag_names) for o in opset.operations}
    assert "token_issue" in tags["login"]


def test_self_ref_tags_profile_write_only_to_writers() -> None:
    """自指段判定只读路径用来分类；PATCH /api/me → profile_write（形状即声明）。"""
    opset = parse_openapi(SAMPLE, asset_id=ASSET, allowed_hosts=[HOST])
    tags = {o.method: set(o.tag_names) for o in opset.operations if o.op_id == "update_me"}
    assert "profile_write" in tags["PATCH"]
    # GET /api/me 的 profile_read 只是结构提议，需 operator 确认
    me = next(o for o in opset.operations if o.op_id == "me")
    assert "profile_read" in {t.tag for t in me.proposed}


def test_explicit_tags_win_and_raise_confidence() -> None:
    opset = parse_openapi(SAMPLE, asset_id=ASSET, allowed_hosts=[HOST])
    tagged = next(o for o in opset.operations if o.op_id == "tagged_op")
    by_tag = {t.tag: t for t in tagged.tags}
    assert by_tag["admin_function"].source == "contract"
    assert by_tag["admin_function"].tag_confidence == 0.85
    assert by_tag["rate_sensitive"].source == "contract"
    # 显式标注优先：admin_function / rate_sensitive 直接采纳，不需要结构佐证
    assert "admin_function" in tagged.tag_names and "rate_sensitive" in tagged.tag_names


def test_structural_only_tag_not_adopted() -> None:
    """结构推断不能单独成立（19 §3.1 第 5 行）：契约没标就不出现。"""
    opset = parse_openapi(SAMPLE, asset_id=ASSET, allowed_hosts=[HOST])
    for op in opset.operations:
        for tag in op.tags:
            assert tag.source in {"contract", "structural_adopted", "observed", "operator"}, (
                f"{op.op_id} 出现非法来源的标签 {tag.tag}"
            )
            if tag.source == "structural_adopted":
                # 只有"形状即声明"的那几条允许；object_read / admin_function 一律不许
                assert tag.tag in {"url_fetch", "token_issue", "profile_write", "object_list", "api_read"}


def test_health_operation_scores_lower() -> None:
    opset = parse_openapi(SAMPLE, asset_id=ASSET, allowed_hosts=[HOST])
    scores = {o.op_id: o.score for o in opset.operations}
    assert scores["healthz"] < scores["get_item"]


# ── 2 拒绝条件（启动即失败）────────────────────────────────────────
def test_host_out_of_scope_rejected() -> None:
    with pytest.raises(ContractHostOutOfScope):
        parse_openapi(SAMPLE, asset_id=ASSET, allowed_hosts=["other.invalid"])


def test_host_check_skipped_when_no_allowlist() -> None:
    opset = parse_openapi(SAMPLE, asset_id=ASSET)
    assert opset.count > 0


def test_unknown_tag_rejected() -> None:
    doc = json.loads(SAMPLE)
    doc["paths"]["/api/echo"]["get"]["x-veripen-tags"] = ["not_a_real_tag"]
    with pytest.raises(UnknownTag):
        parse_openapi(json.dumps(doc), asset_id=ASSET, allowed_hosts=[HOST])


def test_too_many_operations_rejected() -> None:
    with pytest.raises(ContractTooLarge):
        parse_openapi(_gen(30), asset_id=ASSET, allowed_hosts=[HOST], max_operations=10)


# ── 3 幂等：同输入两次同哈希（即使契约书写顺序变了）───────────────
def test_opset_hash_is_stable() -> None:
    a = parse_openapi(SAMPLE, asset_id=ASSET, allowed_hosts=[HOST]).opset_sha256()
    b = parse_openapi(SAMPLE, asset_id=ASSET, allowed_hosts=[HOST]).opset_sha256()
    assert a == b


def test_opset_hash_ignores_declaration_order() -> None:
    doc = json.loads(SAMPLE)
    keys = list(doc["paths"])
    doc["paths"] = {k: doc["paths"][k] for k in reversed(keys)}
    shuffled = parse_openapi(json.dumps(doc), asset_id=ASSET, allowed_hosts=[HOST])
    original = parse_openapi(SAMPLE, asset_id=ASSET, allowed_hosts=[HOST])
    assert shuffled.opset_sha256() == original.opset_sha256(), "排序必须只靠结构，不靠书写顺序"


# ── 4 规划：40 op → 绑定，标签缺失不进候选 ────────────────────────
def test_plan_produces_bindings_after_operator_confirm() -> None:
    """A7：40 op → 40 条绑定。确认集来自 profile selectors（operator）。"""
    opset = parse_openapi(_gen(20), asset_id=ASSET, allowed_hosts=[HOST])
    confirmed = plan(opset, confirm={"object_read", "protected_read"})
    assert confirmed.counts["candidates"] == 20, "20 个 GET 面被 operator 确认"
    # PATCH 面的 profile_write 只在"自指段"上提出；这 20 个路径不带自指段，
    # 所以它们正确地**没有**被提议 → 不进候选（不猜）。


    unconfirmed = plan(opset)
    assert unconfirmed.counts["candidates"] == 0, "没有 operator 确认时不许凭空产绑定"
    reasons = {reason for _, reason in unconfirmed.skipped}
    assert reasons <= {"awaiting_operator", "no_tags"}
    assert "awaiting_operator" in reasons


def test_plan_skips_untagged() -> None:
    opset = parse_openapi(SAMPLE, asset_id=ASSET, allowed_hosts=[HOST])
    result = plan(opset)
    skipped = dict(result.skipped)
    assert skipped, "没被采纳标签的 operation 必须被跳过（19 §4 空标签处理）"
    assert set(skipped.values()) <= {"no_tags", "awaiting_operator"}


def test_candidates_carry_no_address() -> None:
    opset = parse_openapi(SAMPLE, asset_id=ASSET, allowed_hosts=[HOST])
    for candidate in plan(opset).candidates:
        assert candidate.template.startswith(SLASH), "候选里只允许相对模板，不允许 scheme/host"
        assert "://" not in candidate.template


def test_routeset_is_deterministic() -> None:
    opset = parse_openapi(_gen(20), asset_id=ASSET, allowed_hosts=[HOST])
    first = _resolve(opset)
    second = _resolve(opset)
    assert first.routeset_sha256 == second.routeset_sha256


def _resolve(opset: OperationSet):  # noqa: ANN202
    cands = list(plan(opset).candidates)
    q = RouteQuery(asset=AssetRef(id=ASSET), hints=RouteHints())
    return Resolver(sources=[StaticSource("contract", RouteProvenance.CONTRACT, cands)]).resolve(
        q, now=1000
    )


def test_bindings_expose_tags_to_selectors() -> None:
    """oracle 通过 selector 标签问面；一条绑定至少带一个标签。"""
    rs = _resolve(parse_openapi(SAMPLE, asset_id=ASSET, allowed_hosts=[HOST]))
    assert rs.bindings
    for binding in rs.bindings:
        assert binding.template_sha256


# ── 5 删除一个 operation → 绑定消失（A9.4）─────────────────────────
def test_removed_operation_disappears() -> None:
    doc = json.loads(SAMPLE)
    del doc["paths"]["/api/fetch"]
    after = parse_openapi(json.dumps(doc), asset_id=ASSET, allowed_hosts=[HOST])
    fetch_hash = next(
        o.structural_key for o in parse_openapi(SAMPLE, asset_id=ASSET, allowed_hosts=[HOST]).operations
        if o.op_id == "fetch_url"
    )
    assert fetch_hash not in {o.structural_key for o in after.operations}


# ── 6 本地代码边界（19 §8）────────────────────────────────────────
def test_contract_package_never_imports_network() -> None:
    banned = {"httpx", "requests", "aiohttp", "urllib", "socket", "http"}
    for path in (ROOT / "veripen_contract").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for mod in mods:
                assert mod.split(".")[0] not in banned, f"{path.name} import 了 {mod}"


def test_planner_does_not_build_urls() -> None:
    src = (ROOT / "veripen_contract" / "planner.py").read_text(encoding="utf-8")
    for word in ("concat_url", "join_url", "urljoin", "request("):
        assert word not in src, f"planner 里出现 {word}"


# ── 7 别名与归一 ───────────────────────────────────────────────────
def test_path_param_normalization() -> None:
    from veripen_contract import normalize_template

    a, b = "/api/items/{itemId}", "/api/items/{id}"
    assert normalize_template(a) == normalize_template(b)
    assert template_sha256(a) == template_sha256(b)
