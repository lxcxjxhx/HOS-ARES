"""B245 · 交付报告的「现象」不许是内部判定码。

实测一份 `hos-ares run` 报告里，这一整排都在：

```
- 现象：boolean_differential_holds
- 现象：csp_header_absent
- 现象：error_fingerprint_triggered
- 现象：frame_protection_absent
- 现象：nosniff_absent
- 现象：弱面（crawl）：严重度按 medium 计。dom_marker_fired
```

`reason` 是判定码，它本来就单独进 `reason_code` 字段。把它当句子塞进"现象"，
客户读到的是内部标识符——和 B234（chat 降级提示）、B238（hunt 轮次表）同一族。

本用例直接打 `_situation`：有 `assertion.statement` 时用它；只有码时如实说明"没有文字现象"，
而不是把码伪装成一句话。
"""

from __future__ import annotations

from types import SimpleNamespace

from hos_ares_cli.cmd_run import _situation

#: 假 oracle 元数据：severity_rule 走表内（`differential_data_leak` → high，不触发兜底前缀）
_META = SimpleNamespace(severity_rule="differential_data_leak")
#: 强度由**置信度**推出（`strength_of`）：0.9 → contract，0.70 → crawl
_BINDING = SimpleNamespace(ref="route://contract/abc", confidence=0.9)


def test_statement_is_preferred_over_the_reason_code() -> None:
    result = SimpleNamespace(reason="csp_header_absent",
                             assertion={"statement": "响应里没有 CSP 头，且对照面有"})
    text = _situation(_META, "csp_header", _BINDING, result)
    assert "响应里没有 CSP 头" in text, text
    assert "csp_header_absent" not in text, f"判定码又漏进现象里了：{text}"


def test_reason_only_says_it_has_no_prose_instead_of_pretending() -> None:
    result = SimpleNamespace(reason="nosniff_absent", assertion={})
    text = _situation(_META, "nosniff", _BINDING, result)
    assert "未给出文字现象" in text, text
    assert "nosniff_absent" in text, "判定码要留着给对照用（但它不是句子）"


def test_empty_result_yields_empty_text_not_a_dangling_prefix() -> None:
    result = SimpleNamespace(reason="", assertion=None)
    assert _situation(_META, "x", _BINDING, result) == ""


def test_weak_face_prefix_still_leads_the_sentence() -> None:
    weak = SimpleNamespace(ref="route://crawl/abc", confidence=0.7)
    result = SimpleNamespace(reason="dom_marker_fired", assertion={"statement": "页面上出现了探针标记"})
    text = _situation(_META, "xss_marker", weak, result)
    assert text.startswith("弱面（crawl）"), text
    assert "页面上出现了探针标记" in text, text


# ── B329：hunt 的排除也要写进交付物 ────────────────────────────
def test_hunt_md_states_excluded_oracles() -> None:
    from hos_ares.report.hunt import render_hunt_markdown

    md = render_hunt_markdown(
        {"target_host": "a", "source": "contract", "decision": "converged", "reason": "done",
         "oracles": ["sqli_bool_diff"], "excluded": ["mass_assignment"],
         "faces_run": []},
        capsules_dir="capsules", generated_at="t",
    )
    assert "已排除 1 类 oracle" in md, md[:400]
    assert "`mass_assignment`" in md
    assert "不算缺口" in md


def test_hunt_md_has_no_exclusion_line_when_nothing_excluded() -> None:
    from hos_ares.report.hunt import render_hunt_markdown

    md = render_hunt_markdown(
        {"target_host": "a", "source": "contract", "decision": "converged", "reason": "done",
         "oracles": ["sqli_bool_diff"], "faces_run": []},
        capsules_dir="capsules", generated_at="t",
    )
    assert "已排除" not in md


# ── B334：hunt 的 deny 要真拦，且挡下时决策词不许被读成"正常收敛" ──
def test_hunt_md_shows_blocked_note() -> None:
    from hos_ares.report.hunt import render_hunt_markdown

    md = render_hunt_markdown(
        {"target_host": "a", "source": "contract", "decision": "converged", "reason": "x",
         "oracles": [], "faces_run": [],
         "blocked_note": "本轮 3 次被边界挡下，没有一面判成"},
        capsules_dir="capsules", generated_at="t")
    assert "被边界挡下" in md and "不等于「正常收敛」" not in md, md[:400]


def test_hunt_guard_splits_deny_like_run() -> None:
    """`hunt --deny DENY_SET_DEFAULT` 以前照样把 3 个面跑完——它没走 split_deny。

    B335 之后 hunt 直接走 `build_guard`，拆分发生在那里，所以查的是 build_guard。
    """
    import inspect

    from hos_ares_cli import cmd_hunt

    src = inspect.getsource(cmd_hunt.build_guard)
    assert "split_deny(" in src, "统一构造点要拆 deny"
    assert "deny_nets=" in src


# ── B335：ScopeGuard 只许有一个构造点 ───────────────────────────
def test_hunt_builds_its_guard_through_build_guard() -> None:
    """hunt 以前 inline 又建了一个 ScopeGuard，于是 B311 的修复完全没落到它身上。"""
    import inspect
    import re

    from hos_ares_cli import cmd_hunt

    src = inspect.getsource(cmd_hunt.main)
    assert "build_guard(" in src, "hunt 要走统一构造点"
    assert not re.search(r"^\s+scope = ScopeGuard\(", src, re.MULTILINE), \
        "main 里不许再自己 new ScopeGuard"


def test_only_one_scope_guard_construction_site() -> None:
    import inspect
    import re

    from hos_ares_cli import cmd_hunt

    src = inspect.getsource(cmd_hunt)
    hits = re.findall(r"scope = ScopeGuard\(", src)
    assert len(hits) == 1, f"构造点应只有 build_guard 里那一个，实际 {len(hits)} 个"
