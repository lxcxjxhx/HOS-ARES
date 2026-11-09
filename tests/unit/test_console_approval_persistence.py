"""B193：控制台的审批决定只活在本进程里。

判据：`web/console.py` 里那句注释是如实的——"内存里记最后状态（不落库、不碰发现）"。
于是驭智面的审批入口出现这样的行为：

* 客户点了"批准" → 页面变成已批准 → **重启即失**；
* 审计页（`ui.audit` 读 `task_events`）里没有任何痕迹；
* 报表/库里查不到"谁批的"。

一个看起来是审批的东西，实际只改了页面显示。本轮把它接成真记录：
`build_app(record_decision=...)` 由调用方提供记账回调，`cmd_console` 在有 `--db`
时写一条 `approval` 事件（`who/asset/action_class/decision/reason` 都在 detail 里）；
没接库就**明说**"只在本进程有效"。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

SEP = chr(0x2F)
APPROVALS = SEP + "ui" + SEP + "approvals"
ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))


def _approval(now: float | None = None):  # noqa: ANN202
    from web.console import Approval

    return Approval(
        id="ap-1", action_class="reversible_write", impact="写 profile",
        asset="web-01", requests=3, reversible=True,
        route_ref="route://contract/" + "a" * 16,
        created_at=now if now is not None else time.time(), lease_owner="agent",
    )


def _client(record: Any = None) -> TestClient:
    from web.console import ConsoleState, build_app

    state = ConsoleState(findings=[], approvals=[_approval()])
    return TestClient(build_app(state, record_decision=record))


def test_without_a_recorder_the_decision_is_marked_volatile() -> None:
    client = _client()
    view = client.get(APPROVALS).json()["data"]
    assert view["records"] is False, "没接库时页面必须知道"

    resp = client.post(APPROVALS, json={
        "id": "ap-1", "decision": "approve", "reason": "客户已批", "by": "alice",
    })
    body = resp.json()
    assert resp.status_code == 200
    assert body["persisted"] is False
    assert "只在本进程有效" in body["persist_note"]


def test_with_a_recorder_the_decision_is_reported_as_persisted() -> None:
    seen: list[dict[str, Any]] = []

    def record(approval: dict[str, Any]) -> str:
        seen.append(approval)
        return "task_events(kind=approval)"

    client = _client(record)
    assert client.get(APPROVALS).json()["data"]["records"] is True

    body = client.post(APPROVALS, json={
        "id": "ap-1", "decision": "approve", "reason": "客户已批", "by": "alice",
    }).json()
    assert body["persisted"] is True
    assert "已落库" in body["persist_note"]
    assert seen and seen[0]["decided_by"] == "alice"


def test_a_broken_recorder_is_reported_not_swallowed() -> None:
    def record(_approval: dict[str, Any]) -> str:
        raise OSError("库写不进去")

    body = _client(record).post(APPROVALS, json={
        "id": "ap-1", "decision": "reject", "reason": "不在窗口内", "by": "bob",
    }).json()
    assert body["persisted"] is False
    assert "落库失败" in body["persist_note"] and "OSError" in body["persist_note"]


# ── 真正落库那条：写进去的必须是能查的 ──────────────────────────
def test_cli_recorder_writes_an_auditable_event(tmp_path: Path) -> None:
    from veripen_core.audit_source import load_audit
    from veripen_core.blackboard import EventLog
    from veripen_core.store import Store

    db = tmp_path / "c.db"
    Store(str(db)).init().close()

    def record(approval: dict[str, Any]) -> str:
        store = Store(str(db))
        try:
            EventLog(store).record(
                task_id="console", kind="approval",
                ok=approval.get("status") == "approved",
                detail={"by": approval.get("decided_by"),
                        "asset": approval.get("asset"),
                        "action_class": approval.get("action_class"),
                        "decision": approval.get("status")},
            )
        finally:
            store.close()
        return "task_events(kind=approval)"

    _client(record).post(APPROVALS, json={
        "id": "ap-1", "decision": "approve", "reason": "客户已批", "by": "alice",
    })

    store = Store(str(db))
    try:
        rows = load_audit(store)
    finally:
        store.close()
    assert rows, "决定没有落进 task_events"
    row = rows[-1]
    assert row["what"] == "approval"
    assert row["who"] == "alice", "审计页要能看出谁批的"
    assert row["asset"] == "web-01"
    assert row["ok"] is True
