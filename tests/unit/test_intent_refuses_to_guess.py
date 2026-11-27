"""B604 · 规则模式：**说错话比猜错话好**。

## 背景

`test_chat_intent.py` 覆盖 7 个工具，**缺 `patch`**。补测时发现两个真问题：

| 输入 | 修前 | 修后 |
|---|---|---|
| `批量改 a.txt 和 b.txt` | `read(path=a.txt)` | **明确说不懂** |
| `改 a.txt` | `read(path=a.txt)` | **明确说不懂** |

「改 a.txt」缺「改什么」，「批量改 a.txt 和 b.txt」缺「把 A 改成 B」的结构。
两者都落到「只有路径 → 默认读」上，于是**用户说要改，系统去读**。

读通常还能成功（文件在），**用户会以为改完了**。这比「说不懂」糟得多。

## 本文件的判据

1. 有编辑动词但缺结构 → **明确说不懂**（`plan.ok` 为假，`steps` 为空）
2. 完整的编辑句式仍要工作
3. 不带编辑动词的「只有路径」**仍按读**（最常见的用法，不能误伤）
4. `patch` 工具**不在规则模式的词汇里**——它要求结构化 `patches` 数组，
   自然语言给不出来。这条要显式钉住，免得以后有人「顺手」加个半吊子解析
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hos_ares_core.agent.intent import EDIT_WORDS, parse  # noqa: E402


def _tool(text: str) -> str:
    plan = parse(text)
    return plan.steps[0].tool if plan.steps else ""


def _ok(text: str) -> bool:
    return parse(text).ok


# ── 1：有编辑动词但缺结构 → 明确说不懂 ──────────────────────
@pytest.mark.parametrize("text", [
    "批量改 a.txt 和 b.txt",
    "改 a.txt",
    "修改 out/x.json",
    "替换 a.txt",
])
def test_edit_verb_without_structure_says_it_does_not_understand(text: str) -> None:
    """**不许静默去读。**

    读通常能成功（文件在），用户会以为改完了 —— 静默做错事比说不懂更糟。
    """
    plan = parse(text)
    assert not plan.ok, f"「{text}」被当成了能执行：{plan.steps}"
    assert not plan.steps, plan.steps
    # 提示里要教正确句式，不能只说"不懂"
    assert "改成" in plan.understood_as, plan.understood_as


# ── 2：完整的编辑句式仍工作 ─────────────────────────────────
@pytest.mark.parametrize("text", [
    "把 a.txt 里的 foo 改成 bar",
    "将 notes.md 中的 v1 替换成 v2",
    "把 out/x.json 里的 1 改为 2",
])
def test_complete_edit_phrase_still_works(text: str) -> None:
    assert _tool(text) == "edit", text
    plan = parse(text)
    args = plan.steps[0].args
    assert args["old"] and args["new"], args


# ── 3：不带编辑动词的「只有路径」仍按读（不能误伤）──────────
@pytest.mark.parametrize("text", ["a.txt", "out/x.json", "看下 a.txt", "读 notes.md"])
def test_bare_path_still_means_read(text: str) -> None:
    """**最常见的用法**：只给个路径就是读。这条被误伤的话，日常全废。"""
    assert _tool(text) == "read", text


# ── 4：`patch` 不给半吊子自然语言解析 ───────────────────────
def test_patch_is_not_guessed_from_natural_language() -> None:
    """`patch` 要求结构化 `patches` 数组，自然语言给不出来。

    规则模式**不该猜它**——猜出来的 `patches` 一定是错的，而 patch 是写操作，
    猜错就改坏文件。这条钉住「不猜」，不是「没实现」。
    """
    for text in ("patch a.txt", "批量 patch a.txt 和 b.txt", "多处替换"):
        assert _tool(text) != "patch", f"「{text}」被猜成了 patch —— 猜错的写操作会改坏文件"


# ── 5：编辑动词表非空（判据的前提）──────────────────────────
def test_edit_words_list_is_not_empty() -> None:
    """上面第 1 组判据全靠 `EDIT_WORDS` 命中。它空了那组就会静默变绿。"""
    assert EDIT_WORDS, "编辑动词表空了"
    for word in ("改", "修改", "替换"):
        assert word in EDIT_WORDS, f"{word} 不在 EDIT_WORDS 里"
