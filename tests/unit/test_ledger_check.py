"""B178：账本检查器补两条——重复批次号、以及"引用的文件已经不在"的提醒。

判据来自真实数据（219 条）：

```
重复批次号：B18-pre（两次）—— 第二次是**补记行**（带 supersedes_state + correction），
                         这是 I10「只增不改」的正当用法，不该报错；
文件不存在：B123 → tests/unit/test_csp_oracle.py
                         复核 git 历史：该文件被后来的「响应头三件套」批次删掉了，
                         所以这只是"历史指针过期"，不是记录写错。
```

因此：重复号是**硬错**（除非是补记行），文件缺失只是**提醒**。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LINT = ROOT / "scripts" / "lint"
for extra in (ROOT, LINT):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))


def _check(rows: list[dict], root: Path = ROOT):  # noqa: ANN202
    import ledger_check

    return ledger_check.check(rows, root)


def _row(batch: str, **extra) -> dict:  # noqa: ANN003
    base = {"batch": batch, "state": "done", "dod": "A OK", "files": []}
    base.update(extra)
    return base


def test_duplicate_batch_id_is_a_problem() -> None:
    problems, _stale = _check([_row("B1"), _row("B1")])
    assert any("批次号重复" in p for p in problems), problems


def test_correction_row_is_an_allowed_duplicate() -> None:
    """补记行（I10 只增不改）不算错。"""
    rows = [
        _row("B1"),
        _row("B1", supersedes_state="planned", correction="原记录漏了状态，补一行"),
    ]
    problems, _stale = _check(rows)
    assert not any("批次号重复" in p for p in problems), problems


def test_missing_file_is_a_reminder_not_an_error(tmp_path: Path) -> None:
    (tmp_path / "keep.py").write_text("x", encoding="utf-8")
    rows = [_row("B1", files=["keep.py", "gone.py"])]
    problems, stale = _check(rows, tmp_path)
    assert not problems, problems
    assert stale == ["B1: gone.py"], stale


def test_unknown_state_and_missing_dod_still_fail() -> None:
    problems, _stale = _check([{"batch": "B2", "state": "finished"},
                               {"batch": "B3", "state": "done"}])
    assert any("未知状态" in p for p in problems)
    assert any("DoD" in p for p in problems)
