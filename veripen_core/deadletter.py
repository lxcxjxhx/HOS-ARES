"""B13 · 死信单列（03 状态机 / 04 §7）。

死信不是"失败"的另一种写法，它是**停在哪一步、为什么停、还能不能救**的记录。

四条纪律：

1. **进死信的路径唯一**：``attempt >= max_attempts`` 或 ``error_class == terminal``；
   其它情况不允许直接跳 ``DEAD_LETTER``（否则"重试"这个词就没有意义）；
2. **进死信必须带原因**：``error_class`` + ``error_code``，缺一个就是"不知道为啥死的"；
3. **死信不进 error 统计**：它不是"这次没跑成"，是"重试用完了"；
   混在一起，报告里的 error 率会随着重试策略变化而跳动；
4. **死信可被重新投递，但只能由人**：``redeliver`` 显式重置 attempt 并回 QUEUED，
   它不自动发生——自动重投等于重试上限不存在。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from veripen_core.models import TaskState
from veripen_core.store import Store, now

#: 允许直接进死信的错误分类（03：retryable | terminal | needs_human）
DEAD_CLASSES: frozenset[str] = frozenset({"terminal", "needs_human"})


class DeadLetterError(RuntimeError):
    code = "VP-8019"


@dataclass(frozen=True)
class DeadLetter:
    task_id: str
    attempt: int
    max_attempts: int
    error_class: str
    error_code: str
    reason: str

    @property
    def exhausted(self) -> bool:
        """重试用完（而不是 terminal 直接判死）。报告里要分开写这两种。"""
        return self.attempt >= self.max_attempts

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id, "attempt": self.attempt,
            "max_attempts": self.max_attempts, "error_class": self.error_class,
            "error_code": self.error_code, "reason": self.reason,
            "exhausted": self.exhausted,
        }


def should_dead_letter(
    *, attempt: int, max_attempts: int, error_class: str | None
) -> tuple[bool, str]:
    """判定是否进死信。返回 ``(是否, 原因)``。

    **只有两条路**：重试用完，或错误被标成 terminal/needs_human。
    别的任何情况都不许进——"跳死信"是掩盖重试逻辑缺陷的最短路径。
    """
    if error_class in DEAD_CLASSES:
        return True, f"{error_class}_error"
    if attempt >= max_attempts:
        return True, "attempts_exhausted"
    return False, ""


def to_dead_letter(
    store: Store, task_id: str, *, error_class: str, error_code: str, reason: str = ""
) -> DeadLetter:
    """把任务推进死信。**必须从 FAILED 转**（状态机图上只有这一条边）。"""
    row = store.get_task(task_id)
    if row is None:
        raise DeadLetterError(f"任务 {task_id} 不存在")

    state = str(row["state"])
    if state == TaskState.DEAD_LETTER.value:
        raise DeadLetterError(f"{task_id} 已在死信里：重复投递要显式 redeliver")
    if state != TaskState.FAILED.value:
        raise DeadLetterError(
            f"{task_id} 当前 {state}，只有 FAILED 可以进死信（03 状态机图）"
        )

    attempt = int(row["attempt"] or 0)
    max_attempts = int(row["max_attempts"] or 3)
    ok, why = should_dead_letter(
        attempt=attempt, max_attempts=max_attempts, error_class=error_class
    )
    if not ok:
        raise DeadLetterError(
            f"{task_id}: attempt={attempt}/{max_attempts} 且 error_class={error_class}，"
            "还不到进死信的时候"
        )
    if not error_code.strip():
        raise DeadLetterError(f"{task_id}: 进死信必须带 error_code（否则不知道为啥死的）")

    moved = store.transition(
        task_id, TaskState.FAILED, TaskState.DEAD_LETTER,
        error_class=error_class, error_code=error_code,
    )
    if not moved:
        raise DeadLetterError(f"{task_id}: 迁移被抢（有人先改了状态），本次放弃")

    return DeadLetter(
        task_id=task_id, attempt=attempt, max_attempts=max_attempts,
        error_class=error_class, error_code=error_code, reason=reason or why,
    )


def column(store: Store) -> list[DeadLetter]:
    """死信列表。**单列，不混进其它统计。**"""
    rows = store.conn.execute(
        "SELECT * FROM tasks WHERE state=? ORDER BY updated_at DESC",
        (TaskState.DEAD_LETTER.value,),
    ).fetchall()
    return [
        DeadLetter(
            task_id=str(r["id"]),
            attempt=int(r["attempt"] or 0),
            max_attempts=int(r["max_attempts"] or 3),
            error_class=str(r["error_class"] or ""),
            error_code=str(r["error_code"] or ""),
            reason="terminal_error" if r["error_class"] in DEAD_CLASSES else "attempts_exhausted",
        )
        for r in rows
    ]


def redeliver(store: Store, task_id: str, *, by: str) -> str:
    """人工重新投递：死信 → QUEUED，attempt 归零。

    **只能由人触发**（``by`` 必填）：自动重投等于重试上限不存在。
    """
    if not by.strip():
        raise DeadLetterError("redeliver 必须记录是谁操作的")
    row = store.get_task(task_id)
    if row is None:
        raise DeadLetterError(f"任务 {task_id} 不存在")
    if str(row["state"]) != TaskState.DEAD_LETTER.value:
        raise DeadLetterError(f"{task_id} 不在死信里（当前 {row['state']}）")

    moved = store.transition(
        task_id, TaskState.DEAD_LETTER, TaskState.QUEUED,
        attempt=0, lease_owner=None, lease_until=None,
        error_class=None, error_code=None,
    )
    if not moved:
        raise DeadLetterError(f"{task_id}: 迁移被抢，本次放弃")
    store.conn.execute(
        "INSERT INTO task_events (task_id, ts, kind, ok, detail_json)"
        " VALUES (?,?,?,?,?)",
        (task_id, now(), "plan", 1, '{"redelivered":true,"by":"' + by.replace('"', "") + '"}'),
    )
    store.conn.commit()
    return task_id


def stats(store: Store) -> dict[str, Any]:
    """死信统计：**与 error 统计分开列**。"""
    dead = column(store)
    exhausted = sum(1 for d in dead if d.exhausted)
    terminal = len(dead) - exhausted
    return {
        "dead_total": len(dead),
        "attempts_exhausted": exhausted,
        "terminal": terminal,
        # 明确写出来：死信不属于 error 率的分母
        "counts_as_error": False,
    }


def render_section(store: Store) -> str:
    """报告里的死信章节。**单列，不与"未能验证"混排。**"""
    dead = column(store)
    s = stats(store)
    lines = [
        "## 死信（单列）",
        "",
        f"共 {s['dead_total']} 条：重试耗尽 {s['attempts_exhausted']} / "
        f"终态错误 {s['terminal']}",
        "",
    ]
    if not dead:
        lines.append("无。")
    else:
        lines += ["| 任务 | attempt | 原因 | 分类 |", "|---|---|---|---|"]
        for d in dead:
            lines.append(
                f"| `{d.task_id[:12]}` | {d.attempt}/{d.max_attempts}"
                f" | {d.reason} | {d.error_class or '-'}/{d.error_code or '-'} |"
            )
    lines += ["", "> 死信**不计入 error 率**：它不是「这次没跑成」，是「重试用完了」。"]
    return "\n".join(lines) + "\n"


__all__ = [
    "DEAD_CLASSES",
    "DeadLetter",
    "DeadLetterError",
    "column",
    "redeliver",
    "render_section",
    "should_dead_letter",
    "stats",
    "to_dead_letter",
]
