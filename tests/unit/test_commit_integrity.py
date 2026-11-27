"""B567 · **改了但没进提交**——这类失真没有任何用例能直接抓，但它的痕迹可以。

## 真实事故

B566 我改了三个文件（`client.py` 的数组分支、`lab/model_server.py` 的 4 种 mode、
`test_chat_model.py` 的用例改名），然后执行了：

```bash
git checkout -B b566/model-multi-call origin/main
git checkout b555-560/main-clean -- .    # ← 这一步静默覆盖了工作区
git add -A && git commit
```

提交里只有 **3 个新文件**，`client.py` 与 `test_chat_model.py` 的修改**全部丢失**。
而 `verify.py`、`mypy`、本地用例全都绿——因为新加的用例文件在，它们只是
**测不到已经被还原的旧行为**。

发现它靠的是**在模拟 CI 环境里复跑那套用例**：6 条红了，报错正是"多个 json 对象"。

## 本文件钉住什么

`git checkout <ref> -- .` 这类"整目录覆盖"是**静默的**，没有闸门能防。
能防的是它的**后果**：提交说明里承诺的行为，必须能在 HEAD 里找到对应代码。

所以这里用一条**文本契约**：
`batches.jsonl` 里每条 `done` 批次的 `files` 列表，必须至少有一个文件**真的出现在
该批次对应的提交里**——这需要 git 历史，用例跑不到。

退一步，验能验的：**`client.py` 里那个数组分支必须在**（它是 B566 的核心，
被覆盖过一次）。这条不是通用检查，是把"最容易被静默覆盖的那一处"钉死。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _src(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_multi_call_branch_is_actually_present() -> None:
    """`_extract_payload` 必须真的接受顶层数组（B566 的核心改动）。

    这条用例存在的理由很具体：它的实现在 B566 被 `git checkout -- .` 静默还原过一次，
    而当时所有本地检查都是绿的——因为**新加的用例文件还在，只是测不到旧行为**。
    """
    src = _src("hos_ares_core/agent/client.py")
    assert "isinstance(obj, list)" in src, "顶层数组分支不见了（被覆盖过？）"
    assert "return list(obj)" in src
    # 反证：不许再出现 `return dict(parsed)` 这种把 shape 抹平的写法
    assert "return dict(parsed)" not in src, "`dict(parsed)` 会把多调用转坏"


def test_client_call_returns_shape_not_dict() -> None:
    """`HttpChatClient.__call__` 的返回类型必须是 `Any`，不是 `Mapping`。

    声明成 `Mapping` 会逼着实现 `dict(parsed)`，而那正是 B566 的第二处缺陷。
    """
    src = _src("hos_ares_core/agent/client.py")
    # 不钉完整签名文本（参数名或注解一改就误红，B575）：
    # 只要求「`__call__` 的返回注解不是 Mapping」——那才是这条判据的实质。
    call_defs = [ln for ln in src.splitlines() if ln.strip().startswith("def __call__")]
    assert call_defs, "__call__ 不见了"
    assert any("-> Any:" in ln for ln in call_defs), (
        f"`__call__` 的返回注解还是具体的 Mapping：{call_defs}"
    )


def test_conformance_modes_exist_in_the_fake_server() -> None:
    """假端点必须真的实现那几个 mode——用例引用了不存在的 mode 会静默通过。"""
    src = _src("tests/lab/model_server.py")
    for mode in "extra_field missing_field prose empty null".split():
        assert f'"{mode}"' in src, f"mode {mode} 不见了"


def test_the_old_rejection_test_is_replaced() -> None:
    """旧的 `test_two_calls_rejected` 断言的是**不可达**行为——它必须已被替换。

    留着它会与 B521 的实现方向冲突：一边说"允许一次多个调用"，
    一边断言"必须被拒"。谁都没错，只是有一边永远走不到。
    """
    src = _src("tests/integration/test_chat_model.py")
    # 只查**函数定义**，不查全文：新用例的 docstring 里会提到旧名字（历史说明），
    # 那是好事——写成 `not in src` 会把说明也判成违规（本批自己踩过）。
    assert "def test_two_calls_rejected" not in src, "旧用例又回来了"
    assert "def test_two_calls_are_accepted_and_both_run" in src


def test_worktree_matches_head_for_the_key_files() -> None:
    """工作区里这几个关键文件不能有**未提交**的改动。

    这条抓的正是 B566 那类事故的**前一步**：改了文件却没进提交，
    后面一个 `git checkout -- .` 就全没了。
    """
    key = ("hos_ares_core/agent/client.py tests/lab/model_server.py "
           "tests/integration/test_chat_model.py").split()
    proc = subprocess.run(
        ["git", "status", "--porcelain", "--", *key],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT),
    )
    if proc.returncode != 0:
        pytest.skip("不在 git 仓库里")
    dirty = [ln[3:] for ln in (proc.stdout or "").splitlines() if ln.strip()]
    assert not dirty, f"这几个关键文件有未提交改动（可能被下一步覆盖）：{dirty}"
