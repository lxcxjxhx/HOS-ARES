"""B74 · 报告"面"的显示（H18 判据 A–D）。

缺陷复现：`route_ref[:8]` 对 `route://contract/<hex>` 只能截出 `route://` ——
所有发现的"面"长得一模一样。
"""

from __future__ import annotations

from veripen.report.render import ROUTE_SCHEME, FindingView


def _view(ref: str | None) -> FindingView:
    return FindingView(
        finding_id="F-1", title="t", asset_id="a", oracle_id="sqli_bool_diff",
        cwe="CWE-89", severity="medium", confidence=1.0, route_strength="contract",
        route_ref=ref, capsule_id="sha256:x", self_sha256="y",
    )


# ── A：保留来源段 ──────────────────────────────────────────────
def test_route_scheme_keeps_source_and_id_prefix() -> None:
    ref = ROUTE_SCHEME + "contract/" + "e27578ec10330af0"
    out = _view(ref).route_short
    assert out == ROUTE_SCHEME + "contract/e27578ec"
    assert out != ROUTE_SCHEME, "不能再是只有 scheme"
    assert len(out) > len(ROUTE_SCHEME)


def test_different_sources_are_distinguishable() -> None:
    """两条不同来源的面必须显示不同——这是该字段唯一的信息量。"""
    a = _view(ROUTE_SCHEME + "contract/" + "a" * 16).route_short
    b = _view(ROUTE_SCHEME + "observed/" + "b" * 16).route_short
    assert a != b
    assert "contract" in a and "observed" in b


# ── D：脱敏不放松 ──────────────────────────────────────────────
def test_full_id_not_leaked() -> None:
    long_id = "c" * 64
    out = _view(ROUTE_SCHEME + "contract/" + long_id).route_short
    assert long_id not in out, "完整 id 不该出现在短标识里"
    # scheme + 来源段 + 8 位 id 是上限
    assert out == ROUTE_SCHEME + "contract/" + "c" * 8


# ── B：无 scheme 的 ref 行为不变 ───────────────────────────────
def test_plain_ref_still_truncated_to_eight() -> None:
    assert _view("plain-ref-value").route_short == "plain-re"


# ── C：空 ref ──────────────────────────────────────────────────
def test_empty_ref_is_empty() -> None:
    assert _view(None).route_short == ""
    assert _view("").route_short == ""
