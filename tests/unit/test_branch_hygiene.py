"""B547 · PR 与分支卫生：**merged 不等于进了 main**。

真实事故（本轮）：一条堆叠 PR 链 `#239 ← #238`、`#240 ← #239`…
合 `#238` 进 `main` 时，GitHub 把下游 PR 全标成 `merged`，
而那些提交只进了 `#238` 的**分支**。结果：

```
PR 状态：merged          ← 看起来做完了
main 上：只有 #238 一条   ← B537–B545 九条提交全悬空
```

这类失真**没有任何测试能直接抓到**（它不在代码里），但它的前提条件可以抓：
"分发物里引用的提交必须在 `main` 上可达"。这里钉住的是**仓内可验的那部分**：

1. 账本里每条 `done` 批次点名的文件，必须在当前提交里存在（防止"账本说做了、树里没有"）；
2. 账本批次号**连续且唯一**（堆叠 PR 合并后会断号或重号，这是最早的信号）；
3. `CONTRIBUTING.md` 里那条规矩必须在位（文档不写，下一个人还会堆叠）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / ".hos-ares" / "batches.jsonl"
CONTRIBUTING = ROOT / "CONTRIBUTING.md"


def _rows() -> list[dict[str, Any]]:
    return [json.loads(line) for line in LEDGER.read_text(encoding="utf-8").splitlines() if line.strip()]


# ── 1：批次号连续、唯一 ──────────────────────────────────────
def test_recent_batch_numbers_are_unique() -> None:
    """``CONTIGUOUS_FROM`` 之后不许重号。

    重号是堆叠合并的典型症状：同一条批次从两条链各合一次。
    历史里的 `B18-pre` 重复属早期补记，不在本判据范围内。
    """
    recent = [
        row["batch"] for row in _rows()
        if (m := re.fullmatch(r"B(\d+)", row["batch"])) and int(m.group(1)) >= CONTIGUOUS_FROM
    ]
    dupes = {b for b in recent if recent.count(b) > 1}
    assert not dupes, f"批次号重复（堆叠合并的典型症状）：{sorted(dupes)}"


#: 从这一批起要求号连续。之前的断号（B126 / B448 / B461–B499）是**历史事实**：
#: 早期有过跳号与合并。把它们一并报红，这条用例就永远红，于是没人再看它。
#: 而堆叠 PR 事故产生的是**这一区间内**的整段缺失（B537–B545 曾经九条全空）——
#: 那是它该抓的东西。
CONTIGUOUS_FROM = 536


def test_recent_batch_numbers_have_no_gaps() -> None:
    """``CONTIGUOUS_FROM`` 之后必须连续：断号说明"某一批的提交没进来"。

    **末段例外**：编号可能被"预留后没用"——预分配了号、最后把那一批并进别的批，
    于是账本里出现一个尾洞。这类洞不影响可追溯性（每条 done 都有 DoD 与证据），
    所以只报**中段**的洞：那才是"提交没进来"的形状。
    """
    numbered = sorted(
        int(m.group(1))
        for m in (re.fullmatch(r"B(\d+)", row["batch"]) for row in _rows())
        if m is not None
    )
    window = [n for n in numbered if n >= CONTIGUOUS_FROM]
    if len(window) < 3:
        return
    # 中段 = 除最大号之外的部分；最大号之后的号视为"还没用"
    interior = set(range(window[0], window[-1])) - set(numbered)
    # 单号预留：预分配了号、最后把那一批并进别的批（B546 就是这样并进 B544 的）。
    # 它不影响可追溯性——每条 done 都有自己的 DoD 与证据。
    # 真正要抓的是**整段缺失**（B537–B545 曾经九条全空）：连续两个及以上的洞。
    runs: list[list[int]] = []
    for n in sorted(interior):
        if runs and n == runs[-1][-1] + 1:
            runs[-1].append(n)
        else:
            runs.append([n])
    wholesale = [r for r in runs if len(r) >= 2]
    assert not wholesale, (
        f"B{CONTIGUOUS_FROM} 之后整段断号（那些提交没进 main）：{wholesale}"
    )


# ── 2：账本点名的文件必须真的在树里 ──────────────────────────
def test_done_batches_reference_files_that_exist() -> None:
    """`done` 批次点名的文件不见了 = 账本在说假话。

    只查**最近 40 批**：更早的批次可能被后续批次改名/删除（历史事实已发生，
    不算失真），而那属于 `ledger_check.py` 的 `stale` 提醒范围。
    """
    recent = [row for row in _rows() if row.get("state", "").startswith("done")][-40:]
    missing: list[str] = []
    for row in recent:
        for name in row.get("files") or []:
            if not (ROOT / str(name)).exists():
                missing.append(f"{row['batch']}: {name}")
    assert not missing, f"账本点名但树里没有：{missing}"


# ── 3：账本行必须是合法 JSON 且带必需字段 ────────────────────
def test_every_row_is_well_formed() -> None:
    for row in _rows():
        assert "batch" in row and "state" in row, row
        assert row["state"] in {
            "todo", "doing", "done", "done(legacy-scope)",
            "scaffolded", "blocked", "planned",
        }, row


# ── 4：那条规矩必须写在文档里 ────────────────────────────────
def test_contributing_forbids_stacked_prs() -> None:
    """"别堆叠 PR"如果不写下来，下一个人（或下一轮的我）还会堆叠。"""
    text = CONTRIBUTING.read_text(encoding="utf-8")
    assert "不要堆叠 PR" in text
    assert "merged 就是 merged" in text
    assert "base 是 `main`" in text, "检查项里要有这一条"
