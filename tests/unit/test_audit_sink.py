"""B548 · 工具面审计落库：**跨进程可查**，不只是当场看看。

`ToolSurface.audit()` 返回的是**实例内存里的 list** —— 进程一退就没了。
企业级的审计要求是"能事后查"：谁在什么时候调了什么、允不允许、结果如何。

这一批给 `ToolSurface` 加一个可选的 `audit_sink`，`chat` 把它接到 `task_events`。
复用现成的表（`tool` / `ok` / `detail_json` 三列），不新建表 ——
审计事件本来就是"任务做过的一件事"，与 oracle 事件同类。

四条判据：

1. 给了 sink → **每一条**都落（成功、拒绝、未配置、出错）；
2. 没给 sink → 行为与以前完全一致（不强制）；
3. **sink 失败不许让工具调用失败**（审计是运维问题，不是业务结果）；
4. 落库内容能还原出 `allowed_by` 与 `result`（这是事后定责要的两个字段）。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hos_ares_core.store import Store  # noqa: E402
from hos_ares_core.tools.surface import Impact, SurfaceError, ToolSpec, ToolSurface  # noqa: E402


def _surface(sink: object = None) -> ToolSurface:
    surface = ToolSurface()
    surface.register(ToolSpec("a.read", Impact.READ, "读", None, lambda args: {"v": 1}))
    surface.register(ToolSpec("a.exec", Impact.EXEC, "执行", None, lambda args: {"v": 2}))
    surface.register(ToolSpec("a.missing", Impact.READ, "没实现", None, None))
    if sink is not None:
        surface.audit_sink = sink  # type: ignore[assignment]
    return surface


# ── 1：每一条审计都落 ────────────────────────────────────────
def test_every_outcome_is_piped_to_the_sink() -> None:
    seen: list[dict[str, Any]] = []
    surface = _surface(seen.append)

    surface.invoke("a.read", {})
    with pytest.raises(SurfaceError):
        surface.invoke("a.exec", {}, approved=False)      # approval_required
    surface.invoke("a.exec", {}, approved=True)           # ok
    surface.invoke("a.missing", {})                        # not_configured

    results = [row["result"] for row in seen]
    assert results == ["ok", "approval_required", "ok", "not_configured"], results
    assert len(seen) == len(surface.audit()), "内存与落库条数必须一致"


# ── 2：没给 sink 时行为不变 ─────────────────────────────────
def test_without_a_sink_nothing_changes() -> None:
    surface = _surface()
    surface.invoke("a.read", {})
    assert len(surface.audit()) == 1


# ── 3：sink 失败不许影响调用结果 ────────────────────────────
def test_broken_sink_does_not_break_the_call() -> None:
    def boom(_row: dict[str, Any]) -> None:
        raise OSError("disk full")

    surface = _surface(boom)
    result = surface.invoke("a.read", {})
    assert result["ok"] is True, "审计写不进去不该让工具调用失败"
    assert len(surface.audit()) == 1, "内存那份仍要留"


# ── 4：chat 的落库实现（真写库、真能读回）────────────────────
def test_chat_audit_sink_writes_replayable_rows(tmp_path: Path) -> None:
    """真建库 → 调工具 → **换一个连接**读回来（模拟"另一个进程事后查"）。"""
    from hos_ares_cli.cmd_chat import _audit_to_events

    db = tmp_path / "audit.db"
    store = Store(str(db)).init()
    sink = _audit_to_events(store, task_id="t-1")
    surface = _surface(sink)
    surface.invoke("a.exec", {}, approved=True)
    with pytest.raises(SurfaceError):
        surface.invoke("a.exec", {}, approved=False)
    store.close()

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT task_id, kind, tool, ok, detail_json FROM task_events"
        " WHERE kind='tool' ORDER BY seq"
    ).fetchall()
    conn.close()

    assert len(rows) == 2, [dict(r) for r in rows]
    first = json.loads(rows[0]["detail_json"])
    assert rows[0]["tool"] == "a.exec" and rows[0]["ok"] == 1
    assert first["allowed_by"] == "approval" and first["result"] == "ok"
    second = json.loads(rows[1]["detail_json"])
    assert second["allowed_by"] is None and second["result"] == "approval_required"
    assert rows[1]["ok"] == 0, "被拒的那条 ok 必须是 0"
    assert rows[0]["task_id"] == "t-1"


def test_sink_is_optional_and_keyed_by_task(tmp_path: Path) -> None:
    """没给 `task_id` 就不挂 sink：没有任务号的事件无处归属，写进去只是垃圾。"""
    from hos_ares_cli import cmd_chat

    surface = cmd_chat._chat_surface(ROOT, store=object(), task_id="")
    assert surface.audit_sink is None
