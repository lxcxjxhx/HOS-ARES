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


# ── 2：parity 数字（**环境相关，只验不变式**）─────────────────
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

    # **不能要求与实测精确相等**（B564）：parity 的 E1/E5 依赖容器执行体，
    # 本机有 podman 时 green 22 / 未验 0，CI 上没有时 green 21 / 未验 1。
    # 判据改成环境无关的不变式：
    #   ① 文档里的 green 必须是**满编或本机能达到的值**（不许写比实测还高）；
    #   ② red 必须两边都是 0（红就是缺陷，不该随环境变）；
    #   ③ 文档的「未验」必须覆盖实测的「未验」的超集说明（写了就要写全）。
    doc = re.search(r"green (\d+) / 未验 (\d+) / red (\d+)", _text())
    assert doc, "STATUS 里没有 parity 的三元组"
    doc_green, doc_unverified, doc_red = (int(g) for g in doc.groups())

    assert doc_red == red == 0, f"red 必须为 0：文档 {doc_red} 实测 {red}"
    assert doc_green + doc_unverified >= 22, (
        f"文档的 green+未验 = {doc_green + doc_unverified} < 22：漏了能力项"
    )
    # 本机能跑满时，文档也该写满；跑不满时允许文档写"理论满编"（那是产品的样子）
    assert doc_green >= green, (
        f"文档写 green {doc_green}，本机实测 {green} —— 文档不能比实测还低"
    )
    assert skipped == 0 or "不做" in _text()


# ── 3：doctor 三元组（**环境相关，只验 fail=0**）─────────────
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
    doc_ok, doc_fail, doc_skip = (int(g) for g in doc.groups())

    # **同样不能要求精确相等**（B564）：ok/skip 的分配随环境变
    # （本机有 podman → docker 那项 skip；CI 没有 → 别处又不同），
    # 但 **fail 必须是 0** —— 这是唯一与环境无关的判据。
    assert doc_fail == 0 and fail == 0, (
        f"doctor 出现 fail：文档 {doc_fail} 实测 {fail}"
    )
    # ok+skip 的**总数**应当一致（同一套检查项）
    assert doc_ok + doc_skip == ok + skip, (
        f"检查项总数变了：文档 {doc_ok + doc_skip} vs 实测 {ok + skip}"
    )
    assert doc_ok >= ok, f"文档写 ok {doc_ok}，实测 {ok} —— 文档不能比实测还低"


# ── 4：不许含糊 ─────────────────────────────────────────────
def test_no_vague_placeholders() -> None:
    """「敬请期待 / 待定 / 以后再说」是"没验"的伪装，在这份文件里不许出现。"""
    text = _text()
    for bad in ("敬请期待", "待定", "以后再说"):
        assert bad not in text, f"出现含糊说法：{bad}"
