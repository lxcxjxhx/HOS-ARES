"""API 层测试：`POST /v1/tasks` 幂等、状态机、错误信封（`24 §1`/§3）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from veripen_core import Store, TaskState


@pytest.fixture()
def client() -> TestClient:
    store = Store(":memory:")
    app = create_app(store)
    with TestClient(app) as c:
        yield c
    store.close()


def test_post_tasks_returns_id(client: TestClient) -> None:
    """DoD #2：`curl POST /tasks` 返回 id。"""
    r = client.post(
        "/v1/tasks",
        json={
            "engagement_id": "ENG-2026-001",
            "profile": "demo-a",
            "asset_ids": ["web-01"],
            "oracle_ids": ["sqli_bool_diff"],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["task_id"]
    assert len(body["task_id"]) == 26, "ulid must be 26 chars"
    assert body["state"] == TaskState.QUEUED.value
    assert body["reused"] is False
    assert body["idem_key"].startswith("sha256:")


def test_same_idem_key_returns_same_id(client: TestClient) -> None:
    """DoD #3：两次同 idem_key 同 id，且 `reused=true`、不再产生新行。"""
    payload = {
        "engagement_id": "ENG-2026-001",
        "profile": "demo-a",
        "asset_ids": ["web-01"],
        "oracle_ids": ["sqli_bool_diff"],
    }
    first = client.post("/v1/tasks", json=payload).json()
    second = client.post("/v1/tasks", json=payload).json()

    assert first["task_id"] == second["task_id"]
    assert second["reused"] is True
    assert first["idem_key"] == second["idem_key"]

    listing = client.get("/v1/tasks").json()
    assert len(listing["items"]) == 1, "idempotent submit must not create a second task"


def test_idem_key_changes_with_params(client: TestClient) -> None:
    """params 变了就是不同任务（`01 §6` 的 key 定义含 params_canonical）。"""
    base = {
        "engagement_id": "ENG-2026-001",
        "profile": "demo-a",
        "asset_ids": ["web-01"],
        "oracle_ids": ["sqli_bool_diff"],
    }
    a = client.post("/v1/tasks", json={**base, "params": {"max_variants": 3}}).json()
    b = client.post("/v1/tasks", json={**base, "params": {"max_variants": 4}}).json()
    assert a["task_id"] != b["task_id"]
    assert a["idem_key"] != b["idem_key"]


def test_get_task_roundtrip(client: TestClient) -> None:
    created = client.post(
        "/v1/tasks",
        json={
            "engagement_id": "ENG-2026-001",
            "profile": "demo-a",
            "asset_ids": ["web-01"],
            "oracle_ids": ["sqli_bool_diff"],
        },
    ).json()
    got = client.get(f"/v1/tasks/{created['task_id']}").json()
    assert got["task_id"] == created["task_id"]
    assert got["state"] == TaskState.QUEUED.value
    assert got["budget"]["max_steps"] == 120
    assert got["created_at"].endswith("Z"), "timestamps must be UTC RFC3339"


def test_get_missing_task_returns_envelope(client: TestClient) -> None:
    r = client.get("/v1/tasks/NOPE")
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"].startswith("VP-")
    assert "retryable" in err


def test_invalid_oracle_id_rejected(client: TestClient) -> None:
    """未注册形状的 oracle id 在入口就拒，不等到运行期。"""
    r = client.post(
        "/v1/tasks",
        json={
            "engagement_id": "ENG-1",
            "profile": "demo-a",
            "asset_ids": ["web-01"],
            "oracle_ids": ["BAD-ID!"],
        },
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VP-1001"


def test_healthz(client: TestClient) -> None:
    assert client.get("/v1/healthz").json() == {"status": "ok"}
