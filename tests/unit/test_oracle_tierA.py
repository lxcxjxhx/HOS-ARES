"""B5 TierA×4 判定测试（21 §2 B5 DoD）。

正样本：脆弱态靶场 → 四类各自 ≥1 VERIFIED。
负样本：安全态靶场 → 四类各自 REFUTED（不许 VERIFIED）。
三条件：judge 说 VERIFIED 但框架独立复算不成立时，必须退成 INCONCLUSIVE。
"""

from __future__ import annotations

import pytest

from conftest import Lab
from veripen_oracles import TIER_A, tierA
from veripen_oracles.common.harness import run, three_way_holds
from veripen_core.models import Verdict

#: (oracle_id, selector_tag)
CASES: tuple[tuple[str, str], ...] = (
    ("sqli_bool_diff", "param_injectable"),
    ("idor_cross_account", "object_read"),
    ("ssrf_oob", "url_fetch"),
    ("jwt_replay", "protected_read"),
    ("open_redirect", "redirect_entry"),   # B6
    ("path_traversal", "file_read"),        # B6
)


def _by_id(oracle_id: str):
    for cls in TIER_A:
        if cls.meta.id == oracle_id:
            return cls()
    raise KeyError(oracle_id)


def _run(lab: Lab, oracle_id: str, tag: str):
    binding = lab.binding(tag)
    return run(
        _by_id(oracle_id),
        binding,
        lab.transport,
        symbols=lab.symbols,
        nonce=f"nonce-{oracle_id}",
        token=lab.token,
    )


# ── 正样本：脆弱态必须 VERIFIED ─────────────────────────────────────
@pytest.mark.parametrize(("oracle_id", "tag"), CASES)
def test_vulnerable_is_verified(vulnerable: Lab, oracle_id: str, tag: str) -> None:
    result = _run(vulnerable, oracle_id, tag)
    assert result.verdict is Verdict.VERIFIED, f"{oracle_id}: {result.reason}"
    assert three_way_holds(result.pairs), "框架独立复算必须同样成立"


# ── 负样本：安全态不许 VERIFIED ─────────────────────────────────────
@pytest.mark.parametrize(("oracle_id", "tag"), CASES)
def test_hardened_is_not_verified(hardened: Lab, oracle_id: str, tag: str) -> None:
    result = _run(hardened, oracle_id, tag)
    assert result.verdict is not Verdict.VERIFIED, f"{oracle_id} 在安全态误报为 VERIFIED"


# ── 每条 finding 都带得出三条件指纹 ─────────────────────────────────
@pytest.mark.parametrize(("oracle_id", "tag"), CASES)
def test_verified_carries_assertion(vulnerable: Lab, oracle_id: str, tag: str) -> None:
    result = _run(vulnerable, oracle_id, tag)
    assert result.judgement is not None
    assertion = result.judgement.assertion
    assert assertion["kind"], "assertion.kind 必填（报告与 capsule 都要）"
    assert assertion["fingerprint"], "fingerprint 必填（判定要可回归）"
    assert result.judgement.confidence > 0
    assert result.judgement.route_strength == "contract"


# ── oracle 身份与标签（27 §3.1：只能要标签，不能要地址）─────────────
@pytest.mark.parametrize(("oracle_id", "tag"), CASES)
def test_oracle_declares_tags_not_addresses(oracle_id: str, tag: str) -> None:
    oracle = _by_id(oracle_id)
    assert tag in oracle.selector_tags
    for declared in oracle.selector_tags:
        assert chr(0x2F) not in declared, "标签里不许出现路径片段"
        assert "://" not in declared, "标签里不许出现 scheme"


def test_all_registered() -> None:
    ids = {cls.meta.id for cls in TIER_A}
    assert ids == {c[0] for c in CASES}, "注册表与测试矩阵必须一一对应"
    for cls in TIER_A:
        assert cls.meta.cwe.startswith("CWE-")


# ── 缺对照 → 不许出 VERIFIED（07 §0.2 第②条）───────────────────────
def test_missing_control_is_inconclusive(vulnerable: Lab, monkeypatch: pytest.MonkeyPatch) -> None:
    oracle = tierA.by_id("sqli_bool_diff")
    real_plan = oracle.plan

    def plan_without_control(binding):  # noqa: ANN001, ANN202
        return [v for v in real_plan(binding) if v.role != "control"]

    monkeypatch.setattr(oracle, "plan", plan_without_control)
    result = run(oracle, vulnerable.binding("param_injectable"), vulnerable.transport,
                 symbols=vulnerable.symbols, nonce="n")
    assert result.verdict is Verdict.INCONCLUSIVE
    assert result.reason == "no_control"


# ── 基线异常 → BLOCKED，不是漏洞（07 §0.2 第①条）───────────────────
def test_abnormal_baseline_blocks(hardened: Lab) -> None:
    """安全态里 ssrf 的 fetch 白名单让基线 400 → 环境问题，不是漏洞。"""
    oracle = tierA.by_id("ssrf_oob")
    result = run(oracle, hardened.binding("url_fetch"), hardened.transport, symbols=hardened.symbols)
    assert result.verdict is not Verdict.VERIFIED
