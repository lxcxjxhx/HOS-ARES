"""B562 · STATUS 里的数字必须**当场能对上**，不是写一次就放三年。

`docs/delivery/STATUS.md` 声称「这份文件只写能当场复核的数字」。
但它自己写着 `tests/unit 2137 项`，而实测已经是 2183 —— 差了 46 项。

数字漂移在这类文档里是必然的：每加一批测试它就旧一点。所以判据不该是
「某个具体数字对不对」（那每批都要改），而是：

| 判据 | 理由 |
|---|---|
| 文档里出现的 `tests/unit N 项` 与实测**差不超过 5%** | 允许小滞后，不允许差一个数量级 |
| 文档里出现的 `parity ... green N` 与实测一致 | parity 是能力总账，它错了整份文档都不可信 |
| `doctor` 的 ok/fail/skip 三元组与实测一致 | 环境自检的数字最容易过期 |
| 文档里**不许**出现"待定/敬请期待"这类含糊说法 | 那是"没验"的伪装 |

为什么是 5% 而不是精确相等：精确相等要求每次加测试都改文档，
那条规矩没人会守；5% 能让它滞后十几批，但不会滞后到误导人。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STATUS = ROOT / "docs" / "delivery" / "STATUS.md"


def _text() -> str:
    return STATUS.read_text(encoding="utf-8")


# ── 1：unit 项数（允许 5% 滞后）──────────────────────────────
def test_unit_count_in_status_is_within_5_percent() -> None:
    """实测 collect 一次单测数，与文档里的数字比。

    collect 约 20 秒——但它是这条判据唯一诚实的做法：
    不实测的话，只能靠"上次记得是多少"。
    """
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/unit", "--collect-only"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ROOT), timeout=600,
    )
    actual = sum(1 for line in (proc.stdout or "").splitlines() if "::" in line)
    if actual == 0:
        pytest.skip("collect 没拿到数量（环境问题）")
    m = re.search(r"tests/unit\s+(\d+)\s+项", _text())
    assert m, "STATUS 里没有 `tests/unit N 项` 这一行"
    documented = int(m.group(1))
    drift = abs(actual - documented) / actual
    assert drift <= 0.05, (
        f"STATUS 写 {documented} 项，实测 {actual} 项（差 {drift:.0%}）—— "
        f"这份文件声称「只写能当场复核的数字」"
    )


# ── 2：parity 数字必须精确一致 ──────────────────────────────
def test_parity_numbers_match() -> None:
    """parity 是能力总账：它错了整份文档都不可信，所以要求精确。"""
    proc = subprocess.run(
        [sys.executable, "-m", "hos_ares_cli", "parity"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ROOT), timeout=600,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    m = re.search(r"green (\d+) · 未验 (\d+) · 不做 (\d+) · red (\d+)", out)
    assert m, f"parity 输出格式变了：{out[-300:]}"
    green, unverified, skipped, red = (int(g) for g in m.groups())

    doc = re.search(r"green (\d+) / 未验 (\d+) / red (\d+)", _text())
    assert doc, "STATUS 里没有 parity 的三元组"
    assert int(doc.group(1)) == green, (doc.group(1), green)
    assert int(doc.group(2)) == unverified, (doc.group(2), unverified)
    assert int(doc.group(3)) == red, (doc.group(3), red)
    assert skipped == 0 or "不做" in _text()


# ── 3：doctor 三元组 ────────────────────────────────────────
def test_doctor_triple_matches() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "hos_ares_cli", "doctor"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ROOT), timeout=600,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    m = re.search(r"ok (\d+) / fail (\d+) / skip (\d+)", out)
    assert m, f"doctor 输出格式变了：{out[-300:]}"
    ok, fail, skip = (int(g) for g in m.groups())

    doc = re.search(r"doctor\s+(\d+) ok / (\d+) fail / (\d+) skip", _text())
    assert doc, "STATUS 里没有 doctor 的三元组"
    assert (int(doc.group(1)), int(doc.group(2)), int(doc.group(3))) == (ok, fail, skip), (
        f"文档 {(doc.group(1), doc.group(2), doc.group(3))} vs 实测 {(ok, fail, skip)}"
    )


# ── 4：不许含糊 ─────────────────────────────────────────────
def test_no_vague_placeholders() -> None:
    """「敬请期待 / 待定 / 以后再说」是"没验"的伪装，在这份文件里不许出现。"""
    text = _text()
    for bad in ("敬请期待", "待定", "以后再说"):
        assert bad not in text, f"出现含糊说法：{bad}"
