"""B54 审批界面（`H03 §6` / `H07` B54）。

这是控制台**唯一**的写入点，所以判据必须比别处更硬：
批准/拒绝都要理由 · 自批被拒 · 1h 过期即 EXPIRED · 其余 `ui.*` 仍 405 ·
写入类型只能是"审批决定"（不写发现、不改 verdict）· 拒绝理由进报告附录。
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from veripen.report.audit import AuditInput, render_audit_section
from web.console import (
    APPROVAL_TTL_S,
    Approval,
    ConsoleState,
    build_app,
)

SLASH = chr(0x2F)
UI = SLASH + "ui" + SLASH


def _approval(**kw: object) -> Approval:
    base: dict[str, object] = dict(
        id="a1", action_class="write", impact="exec", asset="web-01",
        requests=3, reversible=True, route_ref="7f3a2c11d9e04b58",
        lease_owner="worker-1", created_at=time.time(),
    )
    base.update(kw)
    return Approval(**base)  # type: ignore[arg-type]


def _client(*approvals: Approval) -> TestClient:
    return TestClient(build_app(ConsoleState(approvals=list(approvals))))


def _post(client: TestClient, **body: object) -> object:
    return client.post(UI + "approvals", json=body)


# ── 列表 ────────────────────────────────────────────────────────
def test_get_lists_pending_with_ttl_and_short_ref() -> None:
    data = _client(_approval()).get(UI + "approvals").json()["data"]
    assert data["pending"] == 1
    row = data["rows"][0]
    assert row["status"] == "pending" and 0 < row["expires_in_s"] <= APPROVAL_TTL_S
    assert row["route"] == "7f3a2c11", "绑定只给前 8 位"


# ── 唯一写入点 ─────────────────────────────────────────────────
def test_approve_requires_reason_and_records_decider() -> None:
    client = _client(_approval())
    assert _post(client, id="a1", decision="approve", reason=" ", by="reviewer").status_code == 400
    ok = _post(client, id="a1", decision="approve", reason="已核对影响面", by="reviewer")
    assert ok.status_code == 200
    row = client.get(UI + "approvals").json()["data"]["rows"][0]
    assert row["status"] == "approved" and row["decided_by"] == "reviewer"


def test_reject_records_reason_for_report_appendix() -> None:
    client = _client(_approval())
    assert _post(client, id="a1", decision="reject", reason="目标不在授权范围",
                 by="reviewer").status_code == 200
    row = client.get(UI + "approvals").json()["data"]["rows"][0]
    assert row["status"] == "rejected" and row["reason"] == "目标不在授权范围"


def test_self_approval_is_forbidden() -> None:
    client = _client(_approval(lease_owner="reviewer"))
    resp = _post(client, id="a1", decision="approve", reason="我是执行体", by="reviewer")
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "self_approval"


def test_expired_approval_cannot_be_decided() -> None:
    old = _approval(created_at=time.time() - APPROVAL_TTL_S - 5)
    client = _client(old)
    assert client.get(UI + "approvals").json()["data"]["rows"][0]["status"] == "expired"
    assert _post(client, id="a1", decision="approve", reason="迟到", by="reviewer").status_code == 409


def test_double_decision_is_rejected() -> None:
    client = _client(_approval())
    assert _post(client, id="a1", decision="approve", reason="ok", by="r1").status_code == 200
    assert _post(client, id="a1", decision="reject", reason="反悔", by="r2").status_code == 409


def test_unknown_id_is_404() -> None:
    assert _post(_client(), id="ghost", decision="approve", reason="x", by="r").status_code == 404


def test_bad_decision_value_is_400() -> None:
    assert _post(_client(_approval()), id="a1", decision="maybe", reason="x",
                 by="r").status_code == 400


# ── 只读边界未被破坏 ───────────────────────────────────────────
_READ_ONLY_OPS = "findings coverage audit routes bounds".split()


@pytest.mark.parametrize("op", _READ_ONLY_OPS)
def test_other_pages_stay_write_closed(op: str) -> None:
    client = _client(_approval())
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)(UI + op).status_code == 405, f"{op} 被打开了写入口"


def test_approvals_put_patch_delete_still_closed() -> None:
    client = _client(_approval())
    for method in ("put", "patch", "delete"):
        assert getattr(client, method)(UI + "approvals").status_code == 405


def test_write_only_accepts_approval_decision() -> None:
    """写入载荷里塞发现相关字段也不会被采用——审批只能改审批。"""
    client = _client(_approval())
    resp = _post(client, id="a1", decision="approve", reason="ok", by="reviewer",
                 findings=[{"id": "F-001", "severity": "high"}], verdict="VERIFIED")
    assert resp.status_code == 200
    data = client.get(UI + "findings").json()["data"]
    assert data["rows"] == [], "审批写入不得产生/修改任何发现"


# ── 拒绝理由进报告附录 ─────────────────────────────────────────
def test_rejection_reason_lands_in_report_appendix() -> None:
    text = render_audit_section(AuditInput(
        task_id="t1",
        rejections=[{"id": "a1", "action_class": "write", "decided_by": "reviewer",
                     "reason": "目标不在授权范围"}],
    ))
    assert "附录：审批拒绝理由" in text
    assert "目标不在授权范围" in text


# ── 前端只在这里写 ────────────────────────────────────────────
def test_frontend_posts_only_to_approvals() -> None:
    page = (__import__("pathlib").Path(__file__).resolve().parents[2]
            / "web" / "static" / "index.html").read_text(encoding="utf-8")
    assert 'fetch("../../ui/approvals"' in page
    assert page.count("method: \"POST\"") == 1, "前端只允许一个写路径"
    assert "renderApprovals" in page
