"""B37 控制台装载器（H07 B37 / H02 §?）。

四条判据：
A 同一产物两次装载 → payload 逐字节相同（确定性）；
B `BLOCKED:` 必须映射成 `blocked`，不许混进 `inconclusive`；
C 空文件 / 文件缺失 → 空状态，页面 200 走空态；
D `hits` 语义：确认=1（一次观测）、未确认=0（跑过没中）、blocked=None（没跑完），**不默认 1**。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from web.console import Queue, build_app
from web.loader import SEVERITY, load_file, load_record, summarize

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "out" / "b30" / "hunt.json"
SLASH = chr(0x2F)
UI = SLASH + "ui" + SLASH


def _record() -> dict[str, object]:
    return {
        "target_host": "asset.invalid",
        "findings": {"xss_reflected_ctx": "VERIFIED"},
        "not_confirmed": {
            "open_redirect": {"verdict": "INCONCLUSIVE", "reason": "three_way_failed"},
            "ssrf_oob": {"verdict": "BLOCKED", "reason": "baseline_abnormal"},
        },
        "bindings": 2,
        "decision": "budget",
        "reason": "budget_exhausted:steps",
    }


def _digest(state: object) -> str:
    client = TestClient(build_app(state))  # type: ignore[arg-type]
    blob = "".join(client.get(UI + op).text for op in ("findings", "unverified", "coverage", "overview"))
    return hashlib.sha256(blob.encode()).hexdigest()


# ── A 确定性 ───────────────────────────────────────────────────
def test_loader_is_deterministic() -> None:
    assert _digest(load_record(_record())) == _digest(load_record(_record()))


def test_loader_order_is_sorted_not_input_order() -> None:
    a = dict(_record())
    b = dict(_record())
    a["not_confirmed"] = {"ssrf_oob": {"verdict": "BLOCKED", "reason": "x"},
                          "open_redirect": {"verdict": "INCONCLUSIVE", "reason": "y"}}
    b["not_confirmed"] = {"open_redirect": {"verdict": "INCONCLUSIVE", "reason": "y"},
                          "ssrf_oob": {"verdict": "BLOCKED", "reason": "x"}}
    assert _digest(load_record(a)) == _digest(load_record(b))


# ── B blocked 必须独立 ─────────────────────────────────────────
def test_loader_blocked_is_not_inconclusive() -> None:
    rows = {r.oracle_id: r for r in load_record(_record()).findings}
    assert rows["ssrf_oob"].verdict == "blocked"
    assert rows["ssrf_oob"].queue is Queue.UNVERIFIED
    assert rows["open_redirect"].verdict == "inconclusive"
    assert rows["ssrf_oob"].reason_code.startswith("BLOCKED")


def test_loader_blocked_never_counts_as_verified() -> None:
    state = load_record(_record())
    stats = summarize(state)
    assert stats == {"total": 3, "verified": 1, "unverified": 2, "blocked": 1,
                     "asset": "asset.invalid"}


# ── D hits 三态 ────────────────────────────────────────────────
def test_loader_hits_states() -> None:
    rows = {r.oracle_id: r for r in load_record(_record()).findings}
    assert rows["xss_reflected_ctx"].hits == 1
    assert rows["xss_reflected_ctx"].hits_state == "single"
    assert rows["open_redirect"].hits == 0 and rows["open_redirect"].hits_state == "zero"
    assert rows["ssrf_oob"].hits is None, "blocked 没跑完，不许记 0"
    assert rows["ssrf_oob"].hits_state == "not_run"


def test_loader_severity_only_on_verified() -> None:
    rows = {r.oracle_id: r for r in load_record(_record()).findings}
    assert rows["xss_reflected_ctx"].severity == SEVERITY["xss_reflected_ctx"]
    assert rows["ssrf_oob"].severity is None, "未验证行不得带 severity 分档（null，不是空串）"


# ── C 空输入安全 ───────────────────────────────────────────────
@pytest.mark.parametrize("path", ["out/__nope__.json"])
def test_loader_missing_file_is_empty_state(path: str) -> None:
    state = load_file(ROOT / path)
    assert state.findings == ()
    client = TestClient(build_app(state))
    assert client.get(UI + "overview").status_code == 200
    assert client.get(UI + "findings").json()["data"]["rows"] == []
    assert client.get(UI + "coverage").json()["data"]["scope"] == "observed_only"


def test_loader_broken_json_is_empty_state(tmp_path: Path) -> None:
    p = tmp_path / "broken.json"
    p.write_text("{not json", encoding="utf-8")
    assert load_file(p).findings == ()


# ── 判定不被装载器改 ───────────────────────────────────────────
def test_loader_does_not_promote_unknown_verdict() -> None:
    rec = {"target_host": "a.invalid", "findings": {}, "not_confirmed":
           {"sqli_bool_diff": {"verdict": "WEIRD", "reason": "?"}}}
    row = load_record(rec).findings[0]
    assert row.verdict == "inconclusive"
    assert summarize(load_record(rec))["verified"] == 0


# ── 真实产物（存在才跑；out/ 不入库）───────────────────────────
@pytest.mark.skipif(not ARTIFACT.is_file(), reason="out/b30/hunt.json 不存在（out/ 不入库）")
def test_loader_real_artifact_shape() -> None:
    state = load_file(ARTIFACT)
    rows = {r.oracle_id: r for r in state.findings}
    assert rows["xss_reflected_ctx"].verdict == "verified"
    assert rows["ssrf_oob"].verdict == "blocked"
    client = TestClient(build_app(state))
    data = client.get(UI + "findings").json()["data"]
    assert data["shown"] == 1 and data["unverified"] == 2
    assert data["queues"]["now"] == 0
    finding = client.get(UI + "finding?id=F-003").json()["data"]
    assert finding["selected_by"] == "query" and finding["assertion"]["verdict"] == "blocked"


# ── B100：run 的 findings.json 也要能打开 ───────────────────────
def _report_doc(outcome: str = "reported") -> dict:
    return {
        "schema": "veripen.report/v1",
        "engagement": "web-01",
        "findings": [
            {
                "id": "F-idor_cross_account-abc",
                "oracle": "idor_cross_account",
                "title": "idor_cross_account",
                "severity": "high",
                "confidence": 1.0,
                "asset": "web-01",
                "outcome": outcome,
                "reason_code": "",
                "route": {"ref": "route://contract/abc", "strength": "contract"},
                "evidence": {"capsule_id": "c", "replay_status": "OK"},
            }
        ],
    }


def test_loader_accepts_report_findings_list() -> None:
    state = load_record(_report_doc())
    assert len(state.findings) == 1
    row = state.findings[0]
    assert row.oracle_id == "idor_cross_account"
    assert row.route_strength == "contract"
    assert row.route_ref == "route://contract/abc"
    assert summarize(state)["total"] == 1


def test_report_shape_severity_comes_from_policy() -> None:
    """严重度只有一张表（policy）；loader 不再自己抄一份。"""
    from veripen_oracles.policy import severity_for

    assert SEVERITY["idor_cross_account"] == severity_for("idor_cross_account", "cross_account_read")[0]
    assert SEVERITY["open_redirect"] == severity_for("open_redirect", "unvalidated_redirect")[0]


def test_report_shape_unreported_row_has_no_severity() -> None:
    assert load_record(_report_doc(outcome="undetermined")).findings[0].severity is None
