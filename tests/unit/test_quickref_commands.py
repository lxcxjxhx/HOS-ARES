"""B266 · 速查里那 8 条命令，必须真的都能在没有 make 的机器上跑。

`docs/delivery/quickref.md` 与 `dist-checklist.md` 原先写着"没有 make 的机器**一律**用
`python scripts/tasks.py <同名任务>`"，但 Makefile 有 170 个目标、tasks.py 只登记 12 个——
"一律"是句假话。它真正成立的范围只有速查自己列的那 8 条，所以文档收窄到那个范围，
并用本用例把它钉住：**速查列出的每条 make 命令都要有同名任务入口**。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QUICKREF = ROOT / "docs" / "delivery" / "quickref.md"
_CHECKLIST = ROOT / "docs" / "delivery" / "dist-checklist.md"


def _tasks() -> set[str]:
    out = subprocess.run(
        [sys.executable, "scripts/tasks.py", "--list"],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60,
    ).stdout
    return {ln.split()[0] for ln in out.splitlines() if ln.startswith("  ")}


def _quickref_make_targets() -> list[str]:
    text = QUICKREF.read_text(encoding="utf-8")
    block = text.split("```", 2)[1] if "```" in text else text
    return re.findall(r"^\$ make ([\w.-]+)", block, re.MULTILINE)


def test_every_quickref_make_command_has_a_tasks_entry() -> None:
    targets = _quickref_make_targets()
    assert len(targets) >= 7, targets
    missing = [t for t in targets if t not in _tasks()]
    assert not missing, f"速查里的这些命令在没有 make 的机器上跑不了：{missing}"


def test_quickref_does_not_promise_that_every_target_exists() -> None:
    """`一律`/`同名任务` 这类绝对说法要收窄：170 个目标里只有 12 个有任务入口。"""
    text = QUICKREF.read_text(encoding="utf-8")
    assert "一律用" not in text, "又写回绝对说法了"
    assert "12 条" in text, "要写明任务清单的规模，否则读者以为全覆盖"


def test_checklist_points_at_the_task_list() -> None:
    text = _CHECKLIST.read_text(encoding="utf-8")
    assert "tasks.py" in text and "--list" in text, text[:200]


# ── B323：quickref 里对 make 目标的描述要与目标实际做的事一致 ────
def test_verify_report_description_matches_what_it_does() -> None:
    """零地址那半句现在由 lint-noroute 管，不在 verify-report 里。"""
    from pathlib import Path

    text = (Path(__file__).resolve().parents[2] / "docs" / "delivery" / "quickref.md").read_text(
        encoding="utf-8")
    line = next(ln for ln in text.splitlines() if "make verify-report" in ln)
    assert "清单" in line and "哈希" in line, line
    assert "零地址" not in line, "零地址不归这个目标管"
