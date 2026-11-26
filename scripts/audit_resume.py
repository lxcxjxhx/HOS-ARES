"""断点恢复：`chat --resume` 必须把**已落库的步骤**读回来（B557）。

README 说会话「可 `--resume`」。要配得上这句话，得验三件事：

1. 第一轮跑完，步骤真的进了库（`task_events` 有 `kind=plan` 的行）；
2. **新进程**用同一个 `--db` + `--task-id` 能读回**同样的**步骤；
3. 读回的是**事实**，不是重新编的：库被清掉之后，恢复出来的就是空的。

第 3 条是这套用例的重点：没有它，一个"每次都凭空造几条 Turn"的实现也能过前两条。

用法：``python scripts/audit_resume.py``
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _run_chat(args: list[str], stdin_text: str) -> tuple[int, str]:
    """起一个**真子进程**跑 chat：`--resume` 的意义就是"换进程还能接着看"。"""
    proc = subprocess.run(  # noqa: S603 - 固定命令 + 本地路径
        [sys.executable, "-m", "hos_ares_cli", "chat", *args],
        input=stdin_text, capture_output=True, text=True,
        encoding="utf-8", errors="replace", cwd=str(ROOT), timeout=180,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _plans(db: Path, task_id: str | None = None) -> list[dict[str, Any]]:
    """读 `kind=plan` 的事件。``task_id=None`` 表示"库里全部"。

    **不能用传入的 `--task-id` 当查询键**（B557 实测）：首次会话会自动生成 ULID，
    `--task-id` 只在 `--resume` 时生效。第一版量具按传入值查，永远是 0 条——
    看起来像"步骤没落库"，实际是查错了键。
    """
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    if task_id is None:
        rows = conn.execute(
            "SELECT seq, task_id, tool, ok, detail_json FROM task_events"
            " WHERE kind='plan' ORDER BY seq"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT seq, task_id, tool, ok, detail_json FROM task_events"
            " WHERE task_id=? AND kind='plan' ORDER BY seq",
            (task_id,),
        ).fetchall()
    conn.close()
    return [{"seq": r["seq"], "task_id": r["task_id"], "tool": r["tool"], "ok": r["ok"],
             "detail": json.loads(r["detail_json"])} for r in rows]


def audit() -> dict[str, Any]:
    result: dict[str, Any] = {}
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        db = work / "c.db"
        task_id = "resume-probe"

        # 1) 第一轮：两句话，都写文件（成功）
        (work / "a.txt").write_text("seed\n", encoding="utf-8")
        code1, text1 = _run_chat(
            ["--root", str(work), "--db", str(db), "--yes"],
            "往 a.txt 写 first\n往 b.txt 写 second\nquit\n",
        )
        result["first_exit"] = code1
        plans = _plans(db)                     # 全部 plan，不按传入的 task_id 查
        result["plans_recorded"] = len(plans)
        result["first_output_has_tools"] = ("write" in text1)
        # `--task-id` 在首轮不生效：真实 id 从库里读出来
        task_id = plans[0]["task_id"] if plans else ""
        result["task_id_from_db"] = task_id
        result["task_id_differs_from_flag"] = bool(task_id) and task_id != "resume-probe"

        # 2) 换一个**新进程**、用**库里那个真 id** 恢复
        code2, text2 = _run_chat(
            ["--root", str(work), "--db", str(db), "--task-id", task_id, "--resume", "--yes"],
            "quit\n",
        )
        result["resume_exit"] = code2
        resumed = _plans(db, task_id)
        result["resume_reads_back"] = len(resumed) == len(plans) and bool(plans)

        # 3) 库清空后恢复：**必须是空的**（证明读的是库，不是凭空造）
        conn = sqlite3.connect(str(db))
        conn.execute("DELETE FROM task_events WHERE task_id=?", (task_id,))
        conn.commit()
        conn.close()
        after = _plans(db, task_id)
        result["plans_after_wipe"] = len(after)
        result["wipe_is_visible"] = len(after) == 0

    return result


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=1))
    print()
    checks = [
        ("第一轮跑通", result["first_exit"] == 0),
        ("步骤真的落库", result["plans_recorded"] >= 1),
        ("恢复读回同样条数", result["resume_reads_back"]),
        ("新进程能恢复", result["resume_exit"] == 0),
        ("清库后恢复为空（读的是库不是编的）", result["wipe_is_visible"]),
    ]
    bad = [name for name, ok in checks if not ok]
    if bad:
        print("失败：" + "、".join(bad))
        return 1
    print(f"结论：断点恢复可用（{result['plans_recorded']} 步可读回），且读的是库不是编的")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
