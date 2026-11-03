"""B12.3 · workflow DAG 固化（18 §0 波次列 → 显式 DAG；33 §2 九阶段）。

三条不变量在这里落地：
1. **DAG 可加载且无环**——环检测不是"顺手做"，是加载的一部分，有环直接拒；
2. **每阶段声明 `produces`（带 schema）与 `gate`**——门可以"失败"，也可以"跳过"，
   两者都不许写成"没这回事"；
3. **阶段跳过必须落 `task_events`**——跳过是最容易被吞掉的状态，
   不记账就等于这个阶段从没存在过（自检 5/11 的数据来源）。

单一事实源是 ``config/workflow.yaml``；``18 §0`` 的波次列由 ``wave_of`` 派生，
一致性测试比对两边，不从 md 反向解析（md 是给人看的投影，不是数据库）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping

from .blackboard import EVENT_KINDS, EventLog
from .store import Store

class Stage(str, Enum):
    """33 §2 的九阶段。DAG 里的 ``stage`` 只能落在这里，多一个就是架构建错了。"""

    INTAKE = "intake"
    LEASE = "lease"
    DISCOVERY = "discovery"
    PLAN = "plan"
    GATE = "gate"
    EXEC = "exec"
    JUDGE = "judge"
    RECORD = "record"
    CONVERGE = "converge"


STAGES: frozenset[str] = frozenset(s.value for s in Stage)

#: 18 §0 的波次列。``wave-*`` 节点用 ``wave`` 字段声明，一致性测试据此对账。
WAVES: frozenset[str] = frozenset({"W1", "W2", "W3", "W4"})


class WorkflowError(RuntimeError):
    """DAG 不合法：环、未知阶段、未知依赖、gate/produces 缺声明。**fail-closed**。"""

    code = "VP-8003"


@dataclass(frozen=True)
class Produces:
    """阶段产物声明：名字 + schema 身份（不给 schema 就是"产出了一个没人能校验的东西"）。"""

    name: str
    schema: str

    @classmethod
    def parse(cls, raw: Mapping[str, Any], *, phase: str) -> "Produces":
        name = str(raw.get("name") or "")
        schema = str(raw.get("schema") or "")
        if not name or not schema:
            raise WorkflowError(f"{phase}: produces 必须同时给 name 与 schema，实际 {dict(raw)}")
        return cls(name=name, schema=schema)


@dataclass(frozen=True)
class Phase:
    id: str
    stage: str
    needs: tuple[str, ...] = ()
    produces: tuple[Produces, ...] = ()
    #: 门：可失败（``fails``）或可跳过（``skippable``）。空表示硬门——不过就整批作废。
    gate: str = ""
    gate_skippable: bool = False
    #: 仅 ``oracle-wave`` 节点有：18 §0 的波次列
    wave: str = ""
    event_kind: str = "plan"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "stage": self.stage,
            "needs": list(self.needs),
            "produces": [{"name": p.name, "schema": p.schema} for p in self.produces],
            "gate": self.gate,
            "gate_skippable": self.gate_skippable,
            "wave": self.wave,
            "event_kind": self.event_kind,
        }


@dataclass(frozen=True)
class Workflow:
    version: int
    phases: tuple[Phase, ...] = ()
    by_id: Mapping[str, Phase] = field(default_factory=dict)

    # ── 查询 ───────────────────────────────────────────────────
    def order(self) -> list[Phase]:
        """拓扑序；同层按 id 排序，保证同输入同输出。"""
        done: list[Phase] = []
        seen: set[str] = set()
        remaining = {p.id: p for p in self.phases}
        while remaining:
            ready = sorted(
                (p for p in remaining.values() if all(n in seen for n in p.needs)),
                key=lambda p: p.id,
            )
            if not ready:  # pragma: no cover - load 已做环检测，这里是第二道保险
                raise WorkflowError(f"DAG 有环，无法排序：{sorted(remaining)}")
            for p in ready:
                done.append(p)
                seen.add(p.id)
                remaining.pop(p.id)
        return done

    def wave_of(self, phase_id: str) -> str | None:
        """18 §0 的波次列。非 oracle-wave 节点没有波次，返回 None（不是空串）。"""
        p = self.by_id.get(phase_id)
        return (p.wave or None) if p is not None else None

    def by_wave(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {w: [] for w in WAVES}
        for p in self.phases:
            if p.wave:
                out.setdefault(p.wave, []).append(p.id)
        return {w: sorted(v) for w, v in out.items()}


def load(path: str | Path) -> Workflow:
    """从 YAML 加载并校验。**先校验再返回**——调用方拿不到一个"可能有环"的 DAG。"""
    import yaml

    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    version = int(raw.get("version") or 1)
    phases: list[Phase] = []
    for item in raw.get("phases") or []:
        pid = str(item.get("id") or "")
        if not pid:
            raise WorkflowError(f"phase 缺 id：{item}")
        stage = str(item.get("stage") or "")
        if stage not in STAGES:
            raise WorkflowError(f"{pid}: stage={stage!r} 不在九阶段 {list(STAGES)} 里")
        wave = str(item.get("wave") or "")
        if wave and wave not in WAVES:
            raise WorkflowError(f"{pid}: wave={wave!r} 不在 18 §0 的 {list(WAVES)} 里")
        event_kind = str(item.get("event_kind") or "plan")
        if event_kind not in EVENT_KINDS:
            raise WorkflowError(f"{pid}: event_kind={event_kind!r} 不是合法事件类型")
        phases.append(
            Phase(
                id=pid,
                stage=stage,
                needs=tuple(str(n) for n in (item.get("needs") or ())),
                produces=tuple(Produces.parse(x, phase=pid) for x in (item.get("produces") or ())),
                gate=str(item.get("gate") or ""),
                gate_skippable=bool(item.get("gate_skippable") or False),
                wave=wave,
                event_kind=event_kind,
            )
        )
    wf = Workflow(version=version, phases=tuple(phases), by_id={p.id: p for p in phases})
    validate(wf)
    return wf


def validate(wf: Workflow) -> None:
    """六条校验，任一不过即抛。顺序按"先结构后语义"，报错指向具体 phase。"""
    ids = [p.id for p in wf.phases]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        raise WorkflowError(f"phase id 重复：{sorted(dup)}")

    for p in wf.phases:
        for n in p.needs:
            if n not in wf.by_id:
                raise WorkflowError(f"{p.id}: needs 指向不存在的 phase {n!r}")
            if n == p.id:
                raise WorkflowError(f"{p.id}: 自己依赖自己")
        if p.gate and not p.produces:
            raise WorkflowError(f"{p.id}: 声明了 gate 却没说 produces 什么——门在守空气")

    _detect_cycle(wf)

    has_wave = any(p.wave for p in wf.phases)
    if has_wave:
        for w in WAVES:
            if not wf.by_wave().get(w):
                raise WorkflowError(f"声明了波次却缺 {w}：18 §0 的四个波次必须全在")


def _detect_cycle(wf: Workflow) -> None:
    """三色 DFS。找到环就把环本身报出来，而不是只报"有环"。"""
    WHITE, GREY, BLACK = 0, 1, 2
    color: dict[str, int] = {p.id: WHITE for p in wf.phases}
    stack: list[str] = []

    def visit(pid: str) -> None:
        color[pid] = GREY
        stack.append(pid)
        for n in wf.by_id[pid].needs:
            if color.get(n) == GREY:
                at = stack.index(n)
                raise WorkflowError(f"DAG 有环：{' → '.join(stack[at:] + [n])}")
            if color.get(n) == WHITE:
                visit(n)
        stack.pop()
        color[pid] = BLACK

    for p in wf.phases:
        if color[p.id] == WHITE:
            visit(p.id)


# ══════════════════════════════════════════════════════════════════
# 跳过必须落 task_events（33 §2 / 03 §7 自检 11）
# ══════════════════════════════════════════════════════════════════
def record_skip(events: EventLog | Store, *, task_id: str, phase_id: str, reason: str) -> int:
    """阶段跳过记账。返回事件 seq。

    ``reason`` 是必填：跳过而不给理由，等于让后来的人自己猜，
    这跟"没跑"又假装"跑了"是同一种病。
    """
    if not reason or not reason.strip():
        raise WorkflowError(f"{phase_id}: 跳过必须给 reason（自检 11 会抓空理由）")
    log = events if isinstance(events, EventLog) else EventLog(events)
    ev = log.record(
        task_id=task_id,
        kind="plan",
        ok=True,
        detail={"phase": phase_id, "skipped": True, "reason": reason.strip()},
    )
    return ev.seq


def skipped_phases(store: Store, task_id: str) -> set[str]:
    """从事件流反查"哪些阶段被跳过了"——**账本之外没有第二条真相**。"""
    rows = store.conn.execute(
        "SELECT detail_json FROM task_events WHERE task_id=? AND kind='plan'", (task_id,)
    ).fetchall()
    out: set[str] = set()
    for r in rows:
        detail = json.loads(r["detail_json"] or "{}")
        if detail.get("skipped"):
            out.add(str(detail.get("phase") or ""))
    return out


def unmet(done: Iterable[str], wf: Workflow, recorded: set[str]) -> list[str]:
    """收敛检查：跑过的阶段里，哪些 ``produces`` 没人记账。返回 ``phase:product``。"""
    missing: list[str] = []
    for pid in done:
        p = wf.by_id.get(pid)
        if p is None:
            continue
        for prod in p.produces:
            if prod.name not in recorded:
                missing.append(f"{pid}:{prod.name}")
    return missing


__all__ = [
    "STAGES",
    "WAVES",
    "Phase",
    "Produces",
    "Workflow",
    "WorkflowError",
    "load",
    "record_skip",
    "skipped_phases",
    "unmet",
    "validate",
]
