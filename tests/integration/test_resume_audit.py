"""B557 · 断点恢复：`chat --resume` 读的是**库**，不是凭空造的。

README 说会话「可 `--resume`」。要配得上，得验三件事：

1. 第一轮跑完，步骤真的进库（`task_events` 有 `kind=plan` 的行）；
2. **新进程**用同一个 `--db` + 库里那个真 id 能读回**同样条数**；
3. 库清空之后，恢复出来必须是**空的**。

第 3 条是重点：没有它，一个「每次都凭空造几条 Turn」的实现也能过前两条。

## 一个把量具骗过的坑

第一版量具按**传入的 `--task-id`** 查库，永远 0 条 —— 看起来像"步骤没落库"。
实际是：**首轮会自动生成 ULID，`--task-id` 只在 `--resume` 时生效**
（`--help` 里写着"（`--resume` 时指定）"，是我没读）。
真 id 要从库里读出来。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def result() -> dict[str, Any]:
    """跑一次完整的「首轮 → 恢复 → 清库」三段（起真子进程，约 20 秒）。"""
    import importlib

    sys.path.insert(0, str(ROOT / "scripts"))
    mod: Any = importlib.import_module("audit_resume")
    out: dict[str, Any] = mod.audit()
    return out


# ── 1：步骤真的落库 ─────────────────────────────────────────
def test_steps_are_recorded_as_plan_events(result: dict[str, Any]) -> None:
    assert result["first_exit"] == 0, result
    assert result["plans_recorded"] >= 2, f"两句话该有两步，实际 {result['plans_recorded']}"
    assert result["first_output_has_tools"] is True


# ── 2：新进程能读回同样条数 ─────────────────────────────────
def test_new_process_reads_back_the_same_steps(result: dict[str, Any]) -> None:
    assert result["resume_exit"] == 0, result
    assert result["resume_reads_back"] is True, result


# ── 3：读的是库，不是编的（最要紧的一条）──────────────────
def test_resume_reads_from_the_db_not_from_thin_air(result: dict[str, Any]) -> None:
    """清库之后恢复必须为空。

    没有这一条，一个「每次凭空造几条 Turn」的实现也能通过前两条。
    """
    assert result["plans_after_wipe"] == 0, result
    assert result["wipe_is_visible"] is True


# ── 4：那条把量具骗过的行为约定，钉住它 ─────────────────────
def test_task_id_flag_does_not_apply_to_the_first_turn(result: dict[str, Any]) -> None:
    """`--task-id` 只在 `--resume` 时生效；首轮自动生成 ULID。

    这不是缺陷（`--help` 写着"（`--resume` 时指定）"），但它很容易把量具骗过：
    按传入值查库永远是空的。这条用例把这个约定变成可执行的记录。
    """
    assert result["task_id_from_db"], result
    assert result["task_id_differs_from_flag"] is True, result


def test_help_text_says_task_id_is_for_resume() -> None:
    """帮助文本必须写清这个前提，否则读者会以为首轮也能指定。"""
    import subprocess

    proc = subprocess.run(
        [sys.executable, "-m", "hos_ares_cli", "chat", "--help"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ROOT), timeout=120,
    )
    text = (proc.stdout or "") + (proc.stderr or "")
    assert "--task-id" in text
    assert "resume" in text.lower(), "帮助里要说明它只在 --resume 时生效"
