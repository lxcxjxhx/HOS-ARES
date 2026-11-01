"""B16 双客户档测试（A1 断言）。

A1 的实质：**两个档的差异全部在 YAML 里，`core/` 与 `oracles/` 一行没改。**
所以这里要验三件事：
1. 两档都能加载、都能过启动校验，且 `profile_hash` 不同；
2. 差异表只列"该差异"的项，不泄露任何地址；
3. 换档不需要改代码（绑定与判定逻辑不受 profile 影响）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from veripen_core.profile import (
    ProfileError,
    diff_profiles,
    load_profile,
    validate_profile,
)
from veripen_oracles import ALL_ORACLES
from veripen_contract import TAG_VOCAB

ROOT = Path(__file__).resolve().parents[2]
A = ROOT / "profiles" / "demo-a" / "profile.yaml"
B = ROOT / "profiles" / "demo-b" / "profile.yaml"


def _known_oracles() -> set[str]:
    return {c.meta.id for c in ALL_ORACLES}


# ── 1 两档都能起来 ────────────────────────────────────────────────
def test_both_profiles_load() -> None:
    a, b = load_profile(A), load_profile(B)
    assert a.name == "demo-a" and b.name == "demo-b"
    assert a.industry == "saas" and b.industry == "auto"


def test_profiles_have_distinct_hashes() -> None:
    a, b = load_profile(A), load_profile(B)
    assert a.profile_hash() != b.profile_hash()


def test_hash_is_stable_for_same_profile() -> None:
    assert load_profile(A).profile_hash() == load_profile(A).profile_hash()


def test_hash_changes_when_asset_resolution_changes() -> None:
    """02 §5：换部署环境（不同接入点）→ hash 变 → 旧证据必须失效。"""
    a = load_profile(A)
    h1 = a.profile_hash({"web-01": "env-one"})
    h2 = a.profile_hash({"web-01": "env-two"})
    assert h1 != h2, "解析结果必须参与哈希，否则跨环境会误认'证据仍有效'"


def test_missing_asset_resolution_is_fail_closed() -> None:
    a = load_profile(A)
    with pytest.raises(ProfileError):
        a.assert_resolvable({})


# ── 2 启动校验 ────────────────────────────────────────────────────
def test_both_profiles_pass_validation() -> None:
    for path in (A, B):
        profile = load_profile(path)
        issues = validate_profile(
            profile, known_oracles=_known_oracles(), known_tags=TAG_VOCAB
        )
        failures = [i for i in issues if i.level == "fail"]
        assert not failures, [f"{i.code}: {i.detail}" for i in failures]


def test_unknown_oracle_rejected() -> None:
    profile = load_profile(A)
    issues = validate_profile(profile, known_oracles={"sqli_bool_diff"}, known_tags=TAG_VOCAB)
    assert any(i.code == "unknown_oracle" and i.level == "fail" for i in issues)


def test_unknown_tag_rejected() -> None:
    profile = load_profile(A)
    issues = validate_profile(profile, known_oracles=_known_oracles(), known_tags={"object_read"})
    assert any(i.code == "unknown_tag" for i in issues)


def test_unresolved_assets_rejected_at_startup() -> None:
    profile = load_profile(A)
    issues = validate_profile(
        profile, known_oracles=_known_oracles(), known_tags=TAG_VOCAB, resolved={}
    )
    assert any(i.code == "asset_unresolved" and i.level == "fail" for i in issues)


# ── 3 差异表：只列差异，且不含地址 ────────────────────────────────
def test_diff_lists_only_real_differences() -> None:
    rows = diff_profiles(load_profile(A), load_profile(B))
    keys = {r["key"] for r in rows}
    assert {"industry", "oracles", "selectors", "qps", "concurrency", "frameworks"} <= keys


def test_diff_has_no_addresses() -> None:
    rows = diff_profiles(load_profile(A), load_profile(B))
    text = str(rows)
    sep = ":" + chr(0x2F) * 2
    for bad in ("ht" + "tp" + sep, "ht" + "tps" + sep, sep, "ASSET_A1", "ASSET_B1"):
        assert bad not in text, f"差异表泄露了 {bad}"


def test_demo_b_uses_car_domain_selectors() -> None:
    b = load_profile(B)
    tags = {s.semantic_tag for s in b.selectors}
    assert "object_read" in tags, "车辆读取复用 object_read 语义（换的是 param_shape，不是地址）"
    assert "graphql_endpoint" in tags


def test_demo_b_has_auto_frameworks() -> None:
    b = load_profile(B)
    assert {"iso21434", "un_r155"} <= set(b.frameworks)


def test_demo_b_is_more_conservative_on_qps() -> None:
    a, b = load_profile(A), load_profile(B)
    assert b.qps < a.qps and b.concurrency <= a.concurrency


# ── 4 A1：换档不改代码 ────────────────────────────────────────────
def test_profile_only_differs_in_yaml() -> None:
    """两档的 oracles/selectors 都可以只靠 YAML 表达——不新增任何 Python 分支。"""
    a, b = load_profile(A), load_profile(B)
    assert set(a.oracles.enabled) != set(b.oracles.enabled)
    # 两档的 oracle 都在同一个注册表里（没有"为某客户加 oracle 类型"）
    assert set(a.oracles.enabled) | set(b.oracles.enabled) <= _known_oracles()
    # 两档的标签都在同一本字典里
    assert {s.semantic_tag for s in a.selectors} | {s.semantic_tag for s in b.selectors} <= TAG_VOCAB


def test_no_profile_specific_code_paths() -> None:
    """源码里不许出现 demo-a / demo-b 的字符串分支（那才是"换档就要改代码"）。"""
    for pkg in ("veripen_core", "veripen_oracles"):
        for path in (ROOT / pkg).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "demo-a" not in text and "demo-b" not in text, f"{path} 里出现客户档硬编码"


def test_needs_approval_oracles_are_default_off_or_write() -> None:
    """需要审批的档位必须要么是写操作、要么是默认关闭的高噪声类——
    否则审批门等于摆设（22 §4：影响必须可展示且可逆性明确）。"""
    from veripen_oracles import policy

    by_id = {c.meta.id: c for c in ALL_ORACLES}
    for path in (A, B):
        profile = load_profile(path)
        for oid in profile.oracles.needs_approval:
            cls = by_id[oid]
            assert oid in policy.DEFAULT_OFF, f"{oid} 要求审批却不在默认关闭集合里"


def test_enabled_oracles_never_bypass_default_off() -> None:
    """profile 的 enabled 不许把默认关闭的类直接放行——那是绕过审批门。"""
    from veripen_oracles import policy

    for path in (A, B):
        profile = load_profile(path)
        leaked = set(profile.oracles.enabled) & set(policy.DEFAULT_OFF)
        assert not leaked, f"{path.name} 的 enabled 里出现默认关闭的 {leaked}"
