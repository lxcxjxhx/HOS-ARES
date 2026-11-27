"""B246 · 交付报告正文的三处失真（都在 `render_markdown` 里，均可复现）。

改前实测（用进程内靶场真跑一次 `hos-ares run`，读生成的 report.md）：

```
| high | 7 |                     ← §1 摘要按"全部条目"计数
| reported | 2 |                 ← §0 说只有 2 条出了报告
## 5. 合规映射 / ## 6. 附录      ← 没有合规映射时 §5 整个消失，编号从 4 跳到 6
- … · baseline_abnormal（`baseline_abnormal`）   ← 同一个码印两遍
```

三处都指向同一件事：**读者会以为报告漏了东西**（数字对不上、章节缺号、码重复）。
（另注：B246 第一次落地时误覆盖了 `tests/unit/test_report_sections.py`——
那是 B12.7 的线索/死信用例，已从 HEAD 恢复；本批用例另起文件，不与之杂处。）
"""

from __future__ import annotations

import re
from typing import Any

from hos_ares.report import FindingView, Outcome, ReportInput, render_markdown


def _fv(**kw: Any) -> FindingView:
    # `dict[str, Any]`：`**base` 展开到强类型构造器时 `object` 会让每个参数报 arg-type（B579）。
    base: dict[str, Any] = dict(
        finding_id="f-1", title="对象级授权缺失", asset_id="web-01",
        oracle_id="idor_cross_account", cwe="CWE-639", severity="high",
        confidence=0.95, route_strength="contract",
        route_ref="route://contract/7f3a2c11d9e04b58",
        capsule_id="a" * 64, self_sha256="a" * 64, replay_status="OK",
        situation="secondary 读取 primary 的对象成功，且访问无权对象被拒",
        impact="只读取证", advice="补齐对象级鉴权",
    )
    base.update(kw)
    return FindingView(**base)


def _rep(**kw: Any) -> ReportInput:
    # `dict[str, Any]`：`**base` 展开到强类型构造器时 `object` 会让每个参数报 arg-type（B579）。
    base: dict[str, Any] = dict(
        engagement="ENG-1", profile_hash="h" * 64, image_digest="d" * 64,
        root_sha256="0" * 64, routeset_sha256=["e" * 64], resolver_version="1.0.0",
        findings=(_fv(),), coverage={"zero_hit_tags": []}, frameworks=[],
    )
    base.update(kw)
    return ReportInput(**base)


def _md(report: ReportInput) -> str:
    return render_markdown(report)


# ── 1. 摘要只数"已出报告"的条目 ─────────────────────────────────
def test_severity_summary_counts_only_reported() -> None:
    report = _rep(findings=(
        _fv(),
        _fv(finding_id="f-2", outcome=Outcome.UNDETERMINED, reason_code="baseline_abnormal"),
        _fv(finding_id="f-3", outcome=Outcome.UNDETERMINED, reason_code="baseline_abnormal"),
        _fv(finding_id="f-4", outcome=Outcome.NOT_RUN, reason_code="symbol_missing:SAFE_FILE"),
    ))
    text = _md(report)
    row = next(ln for ln in text.splitlines() if ln.startswith("| high |"))
    assert row.split("|")[2].strip() == "1", f"摘要按全部条目计数了：{row}"
    assert "另有 3 条未判定/未执行" in text, "差额要说明去处"


def test_summary_says_zero_when_nothing_is_reported() -> None:
    report = _rep(findings=(_fv(outcome=Outcome.NOT_RUN, reason_code="SKIPPED"),))
    text = _md(report)
    assert "| （无） | 0 |" in text, text[:400]
    assert "本次没有产出可验证的发现。" in text


# ── 2. 章节号必须连续（§5 缺数据时不许从 4 跳到 6）───────────────
def test_section_numbers_are_contiguous_without_frameworks() -> None:
    text = _md(_rep())
    nums = [int(m.group(1)) for m in (re.match(r"^## (\d+)\.", ln) for ln in text.splitlines()) if m]
    assert nums == list(range(len(nums))), f"章节号不连续：{nums}"


def test_section_numbers_are_contiguous_with_frameworks() -> None:
    text = _md(_rep(frameworks=["owasp", "cwe"]))
    nums = [int(m.group(1)) for m in (re.match(r"^## (\d+)\.", ln) for ln in text.splitlines()) if m]
    assert nums == list(range(len(nums))), f"章节号不连续：{nums}"
    assert "## 5. 合规映射" in text


# ── 3. 判定码不许同一行印两遍 ────────────────────────────────────
def test_unknown_reason_code_is_not_printed_twice() -> None:
    report = _rep(findings=(
        _fv(),
        _fv(finding_id="f-2", outcome=Outcome.UNDETERMINED, reason_code="baseline_abnormal"),
    ))
    text = _md(report)
    dupes = [ln for ln in text.splitlines() if re.search(r"([a-z_]+)（`\1`）", ln)]
    assert not dupes, f"码被印了两遍：{dupes}"
    # B248 起先查人话表：baseline_abnormal 有解释，就以"一句话 · `码`"的形式出现
    line = next(ln for ln in text.splitlines() if "baseline_abnormal" in ln)
    assert "基线响应异常" in line and "`baseline_abnormal`" in line, line
    assert line.count("baseline_abnormal") == 1, line
