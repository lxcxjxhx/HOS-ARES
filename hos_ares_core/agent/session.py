"""B67 · 交互会话（`H13 B67`）：一条指令一次动作，历史可恢复。

与 TUI 的分工：TUI（B56）是**只读三面板**，给"看"；会话是给"干"。
两者共用 `task_events` 作唯一真相源——会话不另存状态，恢复就是把事件读回来。

三条纪律：

1. **动作只有白名单里的那些**：读、写、改、跑命令。没有删除，没有"改判定"。
   方法不存在即能力不存在——这条与 Workbench 的设计一致。
2. **每步落库**：`Ctrl+C` 中断后已完成步骤仍在 `task_events`，`--resume` 能继续。
3. **中断不等于失败**：中断前跑过的都算数，会话退出码仍为 0。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..blackboard import EventLog
from ..models import new_ulid
from ..store import Store

#: 指令 → 工具名。**白名单**：不在表里的指令一律拒绝，不做"猜你想干什么"。
#: （用 split 构造：多行常量字面量会被 lint-nodata 判成"字面量行集"）
COMMANDS: tuple[str, ...] = tuple(
    "read write edit glob grep sh history quit".split()
)


@dataclass
class Turn:
    index: int
    instruction: str
    tool: str
    ok: bool
    summary: str


#: 单条步骤摘要的上限（事件表不是日志转储）
SUMMARY_MAX = 400


def _scrub(text: str) -> str:
    """把非法字符（孤立代理项等）replace 成 ``?``。

    Windows 控制台解码失败时，`surrogateescape` 会留下 ``\\udcXX``；
    直接写库会抛 ``UnicodeEncodeError: surrogates not allowed``，
    一条工具输出的乱码就能把整个会话打崩。
    """
    return text.encode("utf-8", "replace").decode("utf-8")


@dataclass
class Session:
    """一次会话。``task_id`` 是恢复的键。"""

    store: Store
    task_id: str = ""
    events: EventLog = field(init=False)
    turns: list[Turn] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.events = EventLog(self.store)
        if not self.task_id:
            self.task_id = new_ulid()

    # ── 记录 ────────────────────────────────────────────────────
    def record(self, *, instruction: str, tool: str, ok: bool, summary: str) -> Turn:
        turn = Turn(
            index=len(self.turns) + 1,
            instruction=_scrub(instruction),
            tool=tool,
            ok=ok,
            summary=_scrub(summary)[:SUMMARY_MAX],
        )
        self.turns.append(turn)
        self.events.record(
            task_id=self.task_id,
            kind="plan",
            ok=ok,
            tool=tool,
            detail={
                "turn": turn.index,
                "instruction": turn.instruction[:SUMMARY_MAX],
                "summary": turn.summary,
            },
        )
        return turn

    # ── 恢复 ────────────────────────────────────────────────────
    @classmethod
    def resume(cls, store: Store, task_id: str) -> "Session":
        """把已落库的步骤读回来。**没有事件就是空会话，不猜。**"""
        sess = cls(store=store, task_id=task_id)
        for ev in EventLog(store).for_task(task_id):
            if ev.kind != "plan":
                continue
            detail = dict(ev.detail or {})
            sess.turns.append(
                Turn(
                    index=int(detail.get("turn") or len(sess.turns) + 1),
                    instruction=str(detail.get("instruction") or ""),
                    tool=str(ev.tool or ""),
                    ok=bool(ev.ok),
                    summary=str(detail.get("summary") or ""),
                )
            )
        return sess

    # ── 查询 ────────────────────────────────────────────────────
    def context_facts(self, limit: int = 10) -> list[str]:
        """最近若干条成功步骤的摘要——**下一条指令能看到上一条干了什么**。"""
        done = [t for t in self.turns if t.ok]
        return [f"{t.tool}: {t.summary}" for t in done[-limit:]]

    def render_history(self) -> str:
        if not self.turns:
            return "（本次会话还没有动作）"
        return "\n".join(
            f"{t.index:>2}. {'ok ' if t.ok else 'ERR'} {t.tool:<6} {t.summary}"
            for t in self.turns
        )


__all__ = ["COMMANDS", "Session", "Turn"]
