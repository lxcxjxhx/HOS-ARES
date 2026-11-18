"""B370 · oracle 会产出的判定码必须有人话（否则客户报告里出现裸机器词）。

实测：打一个安全靶，`hunt.md` 的「未确认」表里三行并排——

```
| sqli_bool_diff  | REFUTED | `control_also_differs` |
| sqli_error_based| REFUTED | 注入后没有出现数据库错误指纹 · `no_error_fingerprint` |
| xss_dom         | REFUTED | 探针标记没有出现（注入点未触发） · `marker_not_fired` |
```

同一列里两行有解释、一行没有。码表说过"表里没有就原样显示，不猜"——那条纪律对，
但**判定码**（oracle 自己产出的那些）本来就该有人话：它们是产品的声音，不是内部日志。

判据：扫 `hos_ares_oracles/` 里以"结论位置"出现的字符串字面量（`self.verified("x")` /
`self.refuted("x")` / `self.inconclusive("x")` / `ProbeResult(..., "REFUTED", "x")` /
`RunResult(Verdict.X, "x")`），逐条要求码表给得出句子。新增一个判定码而忘了配人话，这里就红。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from hos_ares.report.glossary import explain

ROOT = Path(__file__).resolve().parents[2]
ORACLES = ROOT / "hos_ares_oracles"

_CODE = re.compile(r"[a-z][a-z0-9_]{2,40}")
#: 判定码只可能出现在这几个"结论位置"（正则都锚在调用形态上，不靠启发式上下文）
_PATTERNS = (
    re.compile(r"self\.(?:verified|refuted|inconclusive)\(\s*[\"']([a-z0-9_]+)[\"']"),
    re.compile(r"ProbeResult\([^)]*?,\s*[\"'][A-Z]+[\"']\s*,\s*[\"']([a-z0-9_]+)[\"']"),
    re.compile(r"RunResult\(Verdict\.\w+\s*,\s*[\"']([a-z0-9_]+)[\"']"),
)


def _codes() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in sorted(ORACLES.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in _PATTERNS:
            for code in pattern.findall(text):
                if _CODE.fullmatch(code):
                    found.setdefault(code, set()).add(path.name)
    return found


def test_every_oracle_verdict_code_has_a_human_sentence() -> None:
    codes = _codes()
    assert len(codes) >= 15, f"扫到的判定码只有 {len(codes)} 个，正则可能失效：{sorted(codes)}"
    bare = {code: sorted(f) for code, f in codes.items() if not explain(code)}
    assert not bare, f"这些判定码在报告里会以裸机器词出现：{bare}"


@pytest.mark.parametrize("code", ["control_also_differs", "field_ignored", "no_control"])
def test_the_codes_seen_in_a_real_report_are_explained(code: str) -> None:
    """三条实测撞见过的：安全靶的差分类、只读字段、缺对照。"""
    text = explain(code)
    assert text and not text.startswith("`"), (code, text)


# ── B378：`missing_input:*` 也得有人话 ──────────────────────────
def test_missing_input_family_is_explained() -> None:
    """严格模式下 harness 产 `missing_input:oob:listener`。

    实测：`hos-ares run --strict-requires` 打一个 url_fetch 面，报告里那行是
    「判定码 `missing_input:oob:listener`（机器码：…）」——客户读不出"我缺什么"。
    """
    for code in ("missing_input:oob:listener", "missing_input:auth:secondary"):
        text = explain(code)
        assert text and text.startswith("缺输入"), (code, text)
        assert "没测" in text, (code, text)


def test_harness_missing_input_prefix_is_covered() -> None:
    """码的形状由 harness 决定，这里钉住前缀（改了就红）。"""
    src = (ROOT / "hos_ares_oracles" / "common" / "harness.py").read_text(encoding="utf-8")
    assert 'f"missing_input:{' in src, "harness 的缺输入码格式变了，词表要跟着改"


# ── B406：缺输入时要说清"怎么给" ────────────────────────────────
def test_symbol_hints_name_the_actual_flag() -> None:
    """`symbol_missing:SAFE_TARGET` 只说明缺什么，客户还得自己猜该传哪个参数。

    给每个已知符号一条**可照抄**的提供方式；未知符号给通用写法，不留空。
    """
    from hos_ares_oracles.common.harness import SYMBOL_HINTS, symbol_hint

    assert ">=" not in symbol_hint("SAFE_TARGET").replace("=<", "")
    assert "--token-secondary" in symbol_hint("STRANGER")
    assert "--oob" in symbol_hint("DEAD_HOST")
    # 未知符号也不许留空：没有提示等于让客户去翻源码
    assert symbol_hint("SOMETHING_NEW") == "--symbols SOMETHING_NEW=<值>"
    # 表里每一项都必须给出非空提示（不写死名单，避免"加了新符号忘写提示"）
    assert SYMBOL_HINTS, "提示表为空"
    for name, hint in SYMBOL_HINTS.items():
        assert hint.strip(), f"{name} 没有提示——客户只能去翻源码"


def test_report_sentence_says_how_to_supply_the_symbol() -> None:
    from hos_ares.report.glossary import explain

    text = explain("symbol_missing:SAFE_TARGET")
    assert "SAFE_TARGET" in text
    assert "--symbols" in text, text


# ── B407：说不清"下一步怎么办"的码，等于让客户来问 ──────────────
def test_actionable_sentences_name_the_next_step() -> None:
    """判不了的码要给出**下一步**，守住的码要明说"测了、守住了"。"""
    from hos_ares.report.glossary import explain

    assert "下一步" in explain("baseline_abnormal")
    assert "--repeat" in explain("unstable")
    assert "实现缺陷" in explain("three_way_failed")
    # 变体全被拒 = 防护有效，不能读成"没测"
    assert "守住了" in explain("variants_rejected")
    assert "守住了" in explain("all_variants_rejected")


def test_missing_input_says_which_flag_to_use() -> None:
    from hos_ares.report.glossary import explain

    assert "--token-secondary" in explain("missing_input:auth:secondary")
    assert "--oob" in explain("missing_input:oob:listener")
    assert "--symbols SAFE_FILE" in explain("missing_input:SAFE_FILE")


# ── B408：结论语气必须与判定一致（判否 ≠ 判不了 ≠ 守住了）───────
def _codes_by_verdict() -> dict[str, set[str]]:
    import pathlib
    import re

    root = pathlib.Path("hos_ares_oracles")
    out: dict[str, set[str]] = {"refuted": set(), "inconclusive": set()}
    for f in root.rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        for kind in out:
            out[kind] |= set(re.findall(rf'self\.{kind}\(\s*"([a-z0-9_]+)"', text))
    return out


def test_refuted_sentences_say_the_protection_held() -> None:
    """`REFUTED` 是「测了、不成立」——句子必须读得出这个方向。

    以前 `throttling_observed` 写成「观测到限流生效」，读起来像发现了一个问题；
    其实那是有速率限制这个好消息。同类还有 `no_differential` / `no_callback` 等。
    """
    from hos_ares.report.glossary import explain

    markers = ("守住", "判否", "不成立", "没有副作用")
    bad = [(c, explain(c)) for c in sorted(_codes_by_verdict()["refuted"])
           if not any(m in (explain(c) or "") for m in markers)]
    assert not bad, "判否的句子读不出「守住了」：\n  " + "\n  ".join(f"{c}: {s}" for c, s in bad)


def test_inconclusive_sentences_say_it_could_not_be_judged() -> None:
    from hos_ares.report.glossary import explain

    # 这些说法表达的是同一件事：给不出结论。收口放宽到"语义等价的几种写法"，
    # 免得为了过测试把句子改成套话。
    markers = tuple("判不了 分不出 无从 不成立 不判 无法 不是 不等于 给不了 不足以".split())
    bad = [(c, explain(c)) for c in sorted(_codes_by_verdict()["inconclusive"])
           if not any(m in (explain(c) or "") for m in markers)]
    assert not bad, "判不了的句子读不出「判不了」：\n  " + "\n  ".join(f"{c}: {s}" for c, s in bad)
