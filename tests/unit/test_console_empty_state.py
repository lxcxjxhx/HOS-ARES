"""B242 · 控制台的第一次运行：空库、空产物时每页都要显式空态。

客户装完就打开控制台（没有 `hunt.json`、没有 `task_events.db`）——这是最常见的一次
访问，也最少被用例覆盖（集成用例都在有数据的目录里跑）。

本机实跑过一遍（起真进程 + curl 10 个 op），结论写在这里成为闸门：

| op | 空态长什么样 |
|---|---|
| `overview` | `engagements: []`（**不是** `[{}]`，见 B242）、`findings: 0` |
| `findings` | `total: 0` + 空 `rows` |
| `report` | `present: false` + `empty_hint` |
| `audit` | `source: unavailable`（库不存在时如实说，而不是装作没事件） |
| `bounds` | `count: 0` + `alert: null` |
| 其余 | 各自的 0/空列表，**不是** `{}` 也不是 500 |
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from web import ConsoleState, build_app

SLASH = chr(0x2F)
UI = SLASH + "ui" + SLASH

#: 11 个只读操作里除审批页（POST）外的 10 个 GET
GET_OPS = ("overview findings finding routes coverage unverified bounds report engagement audit").split()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(build_app(ConsoleState()))


@pytest.mark.parametrize("op", GET_OPS)
def test_empty_state_page_answers_200_with_an_envelope(client: TestClient, op: str) -> None:
    resp = client.get(f"{UI}{op}")
    assert resp.status_code == 200, (op, resp.status_code, resp.text[:200])
    body = resp.json()
    assert body["op"] == f"ui.{op}"
    assert isinstance(body["data"], dict), (op, type(body["data"]))
    assert body["payload_version"] == 2, "payload 版本是前端协商的依据"


def test_engagements_is_empty_not_a_blank_record(client: TestClient) -> None:
    """没有 engagement 时 `[{}]` 是一条空记录——和"没有记录"不是同一件事。"""
    data = client.get(f"{UI}overview").json()["data"]
    assert data["engagements"] == [], data["engagements"]
    assert data["findings"] == 0


def test_report_says_absent_instead_of_pretending(client: TestClient) -> None:
    data = client.get(f"{UI}report").json()["data"]
    assert data["present"] is False
    assert data.get("empty_hint"), "缺席要有说明，不能只给空字符串"


def test_audit_marks_the_source_unavailable(client: TestClient) -> None:
    """库不存在时这一页的 `source` 必须是 `unavailable`，不是"零事件"。"""
    data = client.get(f"{UI}audit").json()["data"]
    assert data["source"] == "unavailable", data.get("source")
    assert data["count"] == 0


def test_bounds_is_zero_not_null(client: TestClient) -> None:
    data = client.get(f"{UI}bounds").json()["data"]
    assert data["count"] == 0
    assert data["events"] == []
    assert data["alert"] is None


def test_findings_lists_nothing_without_crashing(client: TestClient) -> None:
    data = client.get(f"{UI}findings").json()["data"]
    assert data["total"] == 0 and data["rows"] == []
    assert data["assets"] == []
