"""B248 · 判定码要有人话，且同一次输出里用同一种写法。

改前实测（真跑 `veripen hunt`）：

```
| `xss_dom` | 1/1 | `5ab365e3` | REFUTED | marker_not_fired |
- `path_traversal`：BLOCKED——SymbolMissing: SAFE_FILE
```

同一份报告里，同一件事有两种写法：表格里裸码、汇总里异常串。客户两边都读不懂。
本轮加 `veripen/report/glossary.py`（码 → 一句话），两处都用它；表里没有的码
**原样显示**，不猜。
"""

from __future__ import annotations

from veripen.report.glossary import explain, explain_with_code


def test_known_code_becomes_a_sentence_with_the_code_kept() -> None:
    text = explain_with_code("marker_not_fired")
    assert text.startswith("探针标记没有出现"), text
    assert "`marker_not_fired`" in text, "码要留着，方便和 findings.json 对照"


def test_unknown_code_is_shown_verbatim_not_invented() -> None:
    assert explain("totally_new_code") == ""
    assert explain_with_code("totally_new_code") == "`totally_new_code`"


def test_symbol_missing_family_is_explained_including_the_symbol() -> None:
    """`symbol_missing:SAFE_FILE` 与异常串 `SymbolMissing: SAFE_FILE` 都算同一族。"""
    for raw in ("symbol_missing:SAFE_FILE", "SymbolMissing: SAFE_FILE"):
        text = explain(raw)
        assert "SAFE_FILE" in text, (raw, text)
        assert "缺输入符号" in text, (raw, text)


def test_empty_code_does_not_crash() -> None:
    assert explain("") == ""
    assert explain_with_code("") == "``"


def test_separator_is_a_dot_not_double_parens() -> None:
    """旧版写成 ``一句话（`code`）``，而多半句子自带括号 → 出现两层括号。"""
    text = explain_with_code("marker_not_fired")
    assert "） · `" in text, text
    assert "）（`" not in text, text


# ── B294：replay 状态也是内部枚举，不许原样进报告 ────────────────
def test_replay_status_gets_a_sentence() -> None:
    from veripen.report.render import replay_text

    assert "未重放" in replay_text("NEVER")
    assert "已重放" in replay_text("OK")
    assert "字节不同" in replay_text("PARTIAL")


def test_unknown_replay_status_is_shown_verbatim() -> None:
    from veripen.report.render import replay_text

    assert replay_text("SOMETHING_NEW") == "SOMETHING_NEW"
    assert replay_text("") == "-"


# ── B295：报告正文里第一次出现的 verdict 词要就地解释 ─────────────
def test_advice_explains_refuted_inline() -> None:
    """报告没有术语表，"判定应变为 REFUTED"对没读过 hunt 报告的人是黑话。"""
    from veripen.report.impact import advice_text

    for severity in ("high", "medium", "low"):
        text = advice_text(severity)
        if "REFUTED" in text:
            assert "REFUTED（测了、不成立）" in text, (severity, text)


# ── B296：报告附录要有术语表 ────────────────────────────────────
def test_report_appendix_lists_the_machine_words() -> None:
    """正文里会出现 reported/REFUTED/NEVER 这些机器词；没有一节统一解释，
    就只能靠在每处就地打括号——那样迟早漏一处。"""
    from veripen.report.render import TERMS

    words = {t for t, _ in TERMS}
    needed = "reported undetermined error not_run VERIFIED REFUTED BLOCKED INCONCLUSIVE NEVER PARTIAL"
    for word in needed.split():
        assert word in words, word
    assert all(meaning.strip() for _, meaning in TERMS)


def test_rendered_report_contains_the_glossary() -> None:
    from veripen.report import FindingView, Outcome, ReportInput, render_markdown

    report = ReportInput(
        engagement="E", profile_hash="h", image_digest="d", root_sha256="a" * 64,
        routeset_sha256=["b"], resolver_version="1", render_time="t", distribution="internal",
        findings=[FindingView(
            finding_id="f-1", title="t", asset_id="a", oracle_id="idor_cross_account",
            cwe="CWE-639", severity="high", confidence=0.9, route_strength="contract",
            route_ref="route://contract/abc", capsule_id="c", self_sha256="c" * 64,
            replay_status="OK", situation="一句话", outcome=Outcome.REPORTED)],
    )
    md = render_markdown(report)
    assert "术语（正文里出现的机器词都在这里）" in md
    assert "`REFUTED`：测了、不成立" in md


# ── B327：--exclude 要在交付物里留痕 ────────────────────────────
def test_report_states_what_was_excluded() -> None:
    """报告只写"适用 2 对"，读者不知道原本有 3 类——操作员主动缩小范围必须写在交付物里。"""
    from veripen.report.render import ReportInput, render_markdown

    report = ReportInput(
        engagement="E", profile_hash="h", image_digest="d", root_sha256="a" * 64,
        routeset_sha256=["b"], resolver_version="1", render_time="t", distribution="internal",
        excluded=["sqli_bool_diff"],
    )
    md = render_markdown(report)
    assert "本次按 `--exclude` 排除 1 类 oracle" in md, md[-400:]
    assert "`sqli_bool_diff`" in md
    assert "不算缺口" in md


def test_no_exclusion_means_no_line() -> None:
    from veripen.report.render import ReportInput, render_markdown

    md = render_markdown(ReportInput(
        engagement="E", profile_hash="h", image_digest="d", root_sha256="a" * 64,
        routeset_sha256=["b"], resolver_version="1", render_time="t", distribution="internal"))
    assert "--exclude" not in md
