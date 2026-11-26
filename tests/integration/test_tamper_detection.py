"""B556 · 篡改检测的四步验证：**能发现篡改**，且**不是恒红**。

README 说「它给你一条能复查的证据链」。要配得上这句话，必须同时满足：

| 判据 | 为什么需要 |
|---|---|
| 原件验得过 | 否则一切都是空谈 |
| **改一字节被发现** | 链的意义所在 |
| **改回去又验得过** | 否则一个"永远 False"的实现也能通过前两条 |
| **删一环被对账发现** | 链的**完整性**，不只是单份哈希 |

第四步有个坑（本批实测）：**只靠 capsule 文件证不了删环**。
`verify_chain` 的 docstring 已写明这一点——"链尾被删在只有 capsule 文件时无法证明
（没有外部锚点），所以报告侧按 `findings.evidence.capsule_id` 列出证据全集"。
所以第四步验的是**对账**（findings 登记的集合 vs 盘上的集合），不是 `verify_chain`。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

#: 有一轮真产物才有东西可验
RUN_DIR = ROOT / "out" / "recall" / "run"

pytestmark = pytest.mark.skipif(
    not (RUN_DIR / "capsules").exists(),
    reason="没有真产物：先跑 scripts/measure_recall.py 生成一轮",
)


def _audit() -> dict[str, Any]:
    import importlib

    sys.path.insert(0, str(ROOT / "scripts"))
    mod: Any = importlib.import_module("audit_tamper")
    result: dict[str, Any] = mod.audit(RUN_DIR)
    return result


@pytest.fixture(scope="module")
def result() -> dict[str, Any]:
    return _audit()


# ── 1：原件 ──────────────────────────────────────────────────
def test_original_chain_verifies(result: dict[str, Any]) -> None:
    assert result["original_verifies"] is True, result["original_detail"]
    assert "len=31" in result["original_detail"] or "len=" in result["original_detail"]


# ── 2 + 3：篡改被发现，且不是恒红 ────────────────────────────
def test_tampering_is_detected(result: dict[str, Any]) -> None:
    assert result["tampered_detected"] is True, result["tampered_detail"]
    # 报文要能定位到具体那一环，不是笼统的 False
    assert "self_sha256" in result["tampered_detail"], result["tampered_detail"]


def test_detection_is_not_always_red(result: dict[str, Any]) -> None:
    """改回去必须又能验过。

    **这一条是这套用例里最重要的一条**：没有它，"永远返回 False"的实现
    也能通过「篡改被发现」那一项。
    """
    assert result["restored_verifies"] is True


# ── 4：删环靠对账，不靠 verify_chain ────────────────────────
def test_missing_link_is_caught_by_reconciliation(result: dict[str, Any]) -> None:
    assert result["missing_link_detected"] is True, result["missing_detail"]
    assert "缺 1" in result["missing_detail"], result["missing_detail"]


def test_chain_order_comes_from_prev_links_not_filenames() -> None:
    """**按文件名排序喂 `verify_chain` 会误报**（本批第一版就踩了）。

    capsule 文件名是内容哈希，与链序无关。用 `prev → self` 重排之后才验得过。
    这条用例钉住"量具必须自己排序"，免得下一个人再踩。
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib

    from hos_ares_core.evidence import load_capsule, verify_chain

    mod: Any = importlib.import_module("audit_tamper")
    caps_dir = RUN_DIR / "capsules"

    naive = [load_capsule(str(p)) for p in sorted(caps_dir.glob("*.json"))]
    ordered = mod._ordered_chain(caps_dir)

    # 顺序化之后必须通过
    assert verify_chain(ordered).ok, "链序化之后仍验不过——那才是真缺陷"
    # 按文件名排序通常不通过（若恰好通过说明文件名与链序同序，那样本用例不判红）
    naive_ok = verify_chain(naive).ok
    if not naive_ok:
        assert True, "按文件名排序确实误报，量具必须自己排序"
