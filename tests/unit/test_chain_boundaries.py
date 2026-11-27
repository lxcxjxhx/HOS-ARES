"""B611 · 证据链的**边界**：哪些篡改它抓不到。

## 为什么要写这个

`test_evidence.py` 有 27 项，覆盖了自哈希、断链、中间篡改。但**没有一条**
明确写出「链尾被删是抓不到的」——那条只出现在 `verify_chain` 的 docstring 里。

B610 的教训是「没验过能红的用例可能永远绿」。这里反过来：
**没写成用例的边界，会在某次重构里被误当成"应该能抓"而改坏。**

## 本文件钉住三件事

1. 删**链尾** → 链条自身判定 ok（**这不是 bug，是设计**：没有外部锚点就无法证）
2. 删**中间** → 断链被抓
3. **外部锚点对账**（`capsule_id` 集合）才是抓链尾缺失的那一层
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests", ROOT / "tests" / "unit"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import capsule_fixture as F  # noqa: E402
from hos_ares_core.evidence import (  # noqa: E402
    CapsuleDoc, chain_capsules, verify_chain,
)


def _three() -> list[CapsuleDoc]:
    return list(chain_capsules([F.capsule(probe_body=x) for x in "abc"]))


# ── 1：删链尾抓不到（设计如此）──────────────────────────────
def test_dropping_the_tail_is_invisible_to_the_chain_itself() -> None:
    """**这条不是缺陷，是边界。**

    链尾没有后继来引用它，所以删掉之后剩下的条之间仍然自洽。
    报告侧靠 `findings.evidence.capsule_id` 列出的**证据全集**对账
    （链尾缺口由那个集合发现）。

    写这条用例的目的：**防止以后有人"修正"成"链尾删除应该报错"** ——
    那会让正常的"只校验一段子链"场景误报。
    """
    caps = _three()
    truncated = caps[:-1]
    assert verify_chain(truncated).ok is True, "删链尾被链条自身发现 —— 与设计不符"


# ── 2：删中间抓得到 ────────────────────────────────────────
def test_dropping_a_middle_capsule_breaks_the_chain() -> None:
    caps = _three()
    holed = [caps[0], caps[2]]
    report = verify_chain(holed)
    assert report.ok is False, "删中间竟然没断链"
    assert any("链断" in b or "prev" in b for b in report.breaks), report.breaks


# ── 3：删链头抓得到（这就是全零检查的用途）──────────────────
def test_dropping_the_head_is_caught_by_the_genesis_check() -> None:
    """`verify_chain` 默认要求链头 `prev` 是全零。

    否则"删掉链头"也会被判 ok —— 剩下的条之间照样自洽。
    """
    caps = _three()
    report = verify_chain(caps[1:])
    assert report.ok is False, "删链头竟然没被发现"
    assert any("不是全零" in b for b in report.breaks), report.breaks


# ── 4：外部锚点对账能抓到链尾缺失 ───────────────────────────
def test_the_external_anchor_set_catches_the_missing_tail() -> None:
    """**这是抓链尾缺失的那一层**：报告里声明的证据全集 vs 实到的文件。

    链条自身抓不到，但"我声明有 3 份证据、实际只到 2 份"是对得上的。
    """
    caps = _three()
    declared = {c.capsule_id for c in caps}
    on_disk = {c.capsule_id for c in caps[:-1]}
    missing = declared - on_disk
    assert len(missing) == 1, missing
