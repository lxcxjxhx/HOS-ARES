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
