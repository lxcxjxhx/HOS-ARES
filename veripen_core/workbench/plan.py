"""B12.16 · 计划与任务层（38 §5，21 §B12.16）。

两条状态机，各管一件事：

**``plan.*``** —— 计划模式。进去之后写/执行全被拒，只留只读动作；
``plan.exit`` 产出计划但**必须由人确认**才解锁。这是状态机，不是提示词开关：
提示词开关靠模型自觉，状态机是 guard 能硬拒的东西。

**``todo.*``** —— 任务清单。与 ``35`` 的线索账本**同构**：
`must_verify_next` 产生的待验证 lead 自动进 todo，于是「线索闭环」在 agent 眼里变成可数的——
``pending_leads == 0`` ⟺ ``todo`` 里没有 ``in_progress``。

那个等价关系是本批的硬 DoD。它不是"顺便对齐"，是两条线能不能对上的唯一证据。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from ..store import Store, now
from .fs import FORBIDDEN_OPS as FS_FORBIDDEN
from .shell import ACTION_REQUIREMENTS, ApprovalPolicy, SandboxMode


class PlanError(RuntimeError):
    code = "VP-8017"


class PlanState(str, Enum):
    NORMAL = "normal"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"


#: 计划模式下**唯一允许**的动作（38 §5.1）。写与执行一律拒。
PLAN_SAFE_OPS: frozenset[str] = frozenset(
    {"fs.read", "fs.glob", "fs.grep", "code.search", "todo.write", "todo.list",
     "todo.update", "plan.exit", "plan.abort",
     # 工具层（tools.registry）的只读工具名——两套词汇都要在这里认得，
     # 否则 dispatch 会因为"表里没有"而把只读工具一起拒掉。
     "list_routes", "list_assets", "record_note", "request_approval"}
)

#: 计划模式明确要拒的动作：留一个白名单外的漏网，整道闸就失效。
PLAN_BLOCKED_OPS: frozenset[str] = frozenset(
    {"fs.write", "fs.edit", "shell.run", "act.http_raw", "act.http_fuzz",
     "act.exec_tool", "act.compose", "task.approve", "task.budget_delta"}
)


@dataclass
class PlanGate:
    """计划模式状态机。**任何写/执行动作在 PLANNING 状态下都过不去。**"""

    state: PlanState = PlanState.NORMAL
    reason: str = ""
    plan_text: str = ""
    actions: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()

    @property
    def is_planning(self) -> bool:
        return self.state is PlanState.PLANNING

    @property
    def unlocked(self) -> bool:
        """只有人确认过才解锁。PLANNING / AWAITING_APPROVAL 都不放行。"""
        return self.state is PlanState.NORMAL

    # ── 状态迁移 ────────────────────────────────────────────────
    def enter(self, *, reason: str) -> None:
        if not reason.strip():
            raise PlanError("plan.enter 需要理由：没有理由的计划模式只是偷懒")
        if self.state is not PlanState.NORMAL:
            raise PlanError(f"已在 {self.state.value} 状态，不能重复进入")
        self.state = PlanState.PLANNING
        self.reason = reason.strip()

    def exit(self, *, plan_text: str, actions: Sequence[str], risks: Sequence[str]) -> None:
        """产出计划 → **进入待确认**。不是直接解锁。"""
        if self.state is not PlanState.PLANNING:
            raise PlanError(f"当前 {self.state.value}，plan.exit 只在 planning 状态可用")
        if not plan_text.strip():
            raise PlanError("plan.exit 需要 plan_text")
        if not actions:
            raise PlanError("plan.exit 需要至少一条 action：空计划等于没计划")
        if not risks:
            raise PlanError("plan.exit 需要至少一条 risk：说不出风险的计划不该被批准")
        self.plan_text = plan_text.strip()
        self.actions = tuple(actions)
        self.risks = tuple(risks)
        self.state = PlanState.AWAITING_APPROVAL

    def approve(self, *, by: str) -> None:
        """**人**确认。这是唯一能解锁的入口。"""
        if self.state is not PlanState.AWAITING_APPROVAL:
            raise PlanError(f"当前 {self.state.value}，没有待确认的计划")
        if not by.strip():
            raise PlanError("approve 必须记录是谁批的")
        self.state = PlanState.NORMAL

    def abort(self) -> None:
        """退出计划模式，**不解锁**（回到 NORMAL 但计划作废）。"""
        self.state = PlanState.NORMAL
        self.plan_text = ""
        self.actions = ()
        self.risks = ()

    # ── 闸门 ────────────────────────────────────────────────────
    def check(self, op: str) -> None:
        """**只要没被批准，写/执行就一律拒。**

        这里必须查 ``unlocked`` 而不是 ``is_planning``：``AWAITING_APPROVAL`` 也是
        "还没批"，只查 planning 会让 ``plan.exit`` 变成自我批准——那是这条状态机存在的
        唯一理由所在，写错一个判断就等于没写。
        """
        if self.unlocked:
            return
        if op in PLAN_SAFE_OPS:
            return
        raise PlanError(
            f"计划模式下不允许 {op}（38 §5.1：只留只读动作；"
            "要执行必须先 plan.exit 并**由人确认**）"
        )

    def deny_reason(self, op: str) -> str | None:
        """给 ``tools.dispatch`` 用的不抛版本：拒就返回理由，放行返回 ``None``。

        工具层要的是"回喂一条结构化错误"，不是异常——异常会让 loop 崩，
        而"计划模式下不许写"是**预期内的拒绝**，不该算崩。
        """
        try:
            self.check(op)
        except PlanError as exc:
            return str(exc)
        return None


# ══════════════════════════════════════════════════════════════════
# todo.*
# ══════════════════════════════════════════════════════════════════
class TodoState(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    BLOCKED = "blocked"

    @property
    def open(self) -> bool:
        """未完成：pending 与 in_progress 都算。"""
        return self in (TodoState.PENDING, TodoState.IN_PROGRESS)


@dataclass(frozen=True)
class TodoItem:
    id: str
    text: str
    state: TodoState = TodoState.PENDING
    evidence_ref: str | None = None
    #: 来自哪条 lead（``35`` 的闭环线索）。没有就是"纯计划项"。
    lead_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "text": self.text, "state": self.state.value,
            "evidence_ref": self.evidence_ref, "lead_id": self.lead_id,
        }


@dataclass
class TodoList:
    """``todo.write`` 是**全量替换**，不是增量——增量写会让"清单"和"实际"悄悄分叉。"""

    items: list[TodoItem] = field(default_factory=list)

    # ── 写 ──────────────────────────────────────────────────────
    def write(self, items: Iterable[TodoItem]) -> None:
        seen: set[str] = set()
        fresh: list[TodoItem] = []
        for it in items:
            if not it.id.strip():
                raise PlanError("todo 项缺 id")
            if not it.text.strip():
                raise PlanError(f"{it.id}: todo 项缺 text")
            if it.id in seen:
                raise PlanError(f"{it.id}: todo id 重复（全量替换时 id 必须唯一）")
            seen.add(it.id)
            fresh.append(it)
        prog = [i for i in fresh if i.state is TodoState.IN_PROGRESS]
        if len(prog) > 1:
            raise PlanError(
                f"同时有 {len(prog)} 条 in_progress：一次只做一件事，"
                "否则「进度」就没有意义"
            )
        self.items = fresh

    # ── 读 ──────────────────────────────────────────────────────
    def items_list(self) -> list[TodoItem]:
        return list(self.items)

    def counts(self) -> dict[str, int]:
        out = {s.value: 0 for s in TodoState}
        for it in self.items:
            out[it.state.value] += 1
        return out

    @property
    def open_count(self) -> int:
        return sum(1 for i in self.items if i.state.open)

    @property
    def in_progress(self) -> list[TodoItem]:
        return [i for i in self.items if i.state is TodoState.IN_PROGRESS]

    # ── 更新 ────────────────────────────────────────────────────
    def update(self, item_id: str, *, state: TodoState,
               evidence_ref: str | None = None) -> TodoItem:
        idx = next((n for n, i in enumerate(self.items) if i.id == item_id), None)
        if idx is None:
            raise PlanError(f"todo 项 {item_id} 不存在")
        target = self.items[idx]
        if target.state is state:
            raise PlanError(f"{item_id} 已是 {state.value}：更新必须产生变化")
        if state is TodoState.DONE and not evidence_ref:
            raise PlanError(f"{item_id}: 标 done 必须给 evidence_ref（「做完了」要有东西能看）")
        if state is TodoState.IN_PROGRESS and self.in_progress:
            raise PlanError("已有 in_progress 项：一次只做一件事")
        updated = replace(target, state=state, evidence_ref=evidence_ref or target.evidence_ref)
        self.items[idx] = updated
        return updated


# ══════════════════════════════════════════════════════════════════
# todo ↔ leads 的一致性（本批硬 DoD）
# ══════════════════════════════════════════════════════════════════
def leads_to_todo(pending_leads: Sequence[Mapping[str, Any]]) -> list[TodoItem]:
    """``must_verify_next`` 的 lead 自动进 todo。

    **只同步 pending**：已闭环的 lead 由它自己的账本记着，todo 不该留副本
    （两份记录迟早说法不同）。
    """
    out: list[TodoItem] = []
    for lead in pending_leads:
        lid = str(lead.get("id") or "")
        if not lid:
            continue
        oracles = lead.get("verifiable_by") or []
        if isinstance(oracles, str):
            import json

            try:
                oracles = json.loads(oracles)
            except ValueError:
                oracles = []
        # todo id 用**完整** lead id：同一毫秒内产生的两个 lead 前 12 位相同，
        # 截断过会让 todo.write 的唯一性检查把两条不同线索判成同一条。
        out.append(
            TodoItem(
                id=f"lead-{lid}",
                text=f"验证线索 {lid[:12]}：可验 by {', '.join(str(o) for o in oracles) or '（无）'}",
                state=TodoState.PENDING,
                lead_id=lid,
            )
        )
    return sorted(out, key=lambda i: i.id)


def check_consistency(todo: TodoList, pending_leads: int) -> tuple[bool, str]:
    """``pending>0`` ⟺ 有 ``in_progress``。

    这是「线索闭环」在 agent 视野里可数的唯一证据：
    - 有未闭环线索，却没有任何 in_progress → 它在瞎逛，没在闭环；
    - 没有未闭环线索，却有 in_progress → 它在做一件查不到出处的事。

    两个方向都要查，只查一边会漏掉另一半。
    """
    prog = len(todo.in_progress)
    if pending_leads > 0 and prog == 0:
        return False, (
            f"有 {pending_leads} 条未闭环线索，但 todo 里没有 in_progress 项"
            "（线索闭环在 agent 视野里不可见）"
        )
    if pending_leads == 0 and prog > 0:
        lead_backed = [i.id for i in todo.in_progress if i.lead_id]
        if lead_backed:
            return False, (
                f"{lead_backed} 挂在已闭环的 lead 上，却仍是 in_progress"
                "（线索状态与 todo 不同步）"
            )
    return True, ""


# ══════════════════════════════════════════════════════════════════
# 统一闸门：把 plan 状态接到 fs/shell 前面
# ══════════════════════════════════════════════════════════════════
def guard_workbench_op(
    plan: PlanGate, op: str, *, sandbox_mode: SandboxMode, approval_policy: ApprovalPolicy,
    todo_locked: bool = False,
) -> None:
    """一个入口管三件事：计划模式、删除能力、双维度策略。

    ``plan.enter`` 之后 ``fs.write`` 被拒——DoD 点名的那条，就在这里。
    """
    if op in FS_FORBIDDEN:
        raise PlanError(f"{op} 不存在：工作台不提供删除/移动能力")
    plan.check(op)
    if todo_locked and op in {"fs.write", "fs.edit", "shell.run"}:
        raise PlanError(f"todo 未收敛时不允许 {op}：先把清单收干净")
    need = ACTION_REQUIREMENTS.get(op)
    if need is None:
        return
    need_mode, need_approval = need
    if sandbox_mode.order < need_mode.order:
        raise PlanError(f"{op}: sandbox_mode={sandbox_mode.value} < 需要 {need_mode.value}")
    if approval_policy.order < need_approval.order:
        raise PlanError(
            f"{op}: approval_policy={approval_policy.value} < 需要 {need_approval.value}"
        )


class TodoStore:
    """``todos`` 表的读写。**全量替换**：``save`` 先清该 task 的旧行再写新行。

    为什么必须是全量替换而不是增量：``TodoList.write`` 的语义就是"这就是当前清单"。
    增量写会让「磁盘上的清单」和「内存里的清单」悄悄分叉，
    而分叉之后**没有任何一方是权威**——恢复运行时读到哪份都可能是错的。

    为什么落库：进程重启后「现在做到哪」必须还在（33 §2 九阶段里最容易断的一环）。
    """

    def __init__(self, store: Store) -> None:
        self.store = store

    def save(self, task_id: str, todo: TodoList) -> int:
        """全量替换。返回写入条数。"""
        if not task_id:
            raise PlanError("todo 落库需要 task_id")
        stamp = now()
        with self.store.lock:
            self.store.conn.execute("DELETE FROM todos WHERE task_id=?", (task_id,))
            for it in todo.items_list():
                self.store.conn.execute(
                    "INSERT INTO todos (task_id, id, text, state, evidence_ref,"
                    " lead_id, updated_at) VALUES (?,?,?,?,?,?,?)",
                    (
                        task_id, it.id, it.text, it.state.value, it.evidence_ref,
                        it.lead_id, stamp,
                    ),
                )
            self.store.conn.commit()
        return len(todo.items_list())

    def load(self, task_id: str) -> TodoList:
        """读回清单。**空表返回空清单**，不是 None——"没有计划"也是一种状态。"""
        rows = self.store.conn.execute(
            "SELECT * FROM todos WHERE task_id=? ORDER BY rowid", (task_id,)
        ).fetchall()
        items = [
            TodoItem(
                id=str(r["id"]),
                text=str(r["text"]),
                state=TodoState(str(r["state"])),
                evidence_ref=r["evidence_ref"],
                lead_id=r["lead_id"],
            )
            for r in rows
        ]
        return TodoList(items=items)

    def counts(self, task_id: str) -> dict[str, int]:
        out = {s.value: 0 for s in TodoState}
        rows = self.store.conn.execute(
            "SELECT state, COUNT(*) AS n FROM todos WHERE task_id=? GROUP BY state", (task_id,)
        ).fetchall()
        for r in rows:
            out[str(r["state"])] = int(r["n"])
        return out


__all__ = [
    "PLAN_BLOCKED_OPS",
    "PLAN_SAFE_OPS",
    "PlanError",
    "PlanGate",
    "PlanState",
    "TodoItem",
    "TodoList",
    "TodoState",
    "TodoStore",
    "check_consistency",
    "guard_workbench_op",
    "leads_to_todo",
]
