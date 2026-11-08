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

# ── B115：控制台吃目录，资产名取自账本 ───────────────────────────
def _write(tmp_path: "object", name: str, payload: dict) -> None:
    import json
    from pathlib import Path

    (Path(tmp_path) / name).write_text(json.dumps(payload), encoding="utf-8")


def test_loader_accepts_a_directory(tmp_path: object) -> None:
    """用户敲 --hunt out/run-xxx 时给的是目录。"""
    _write(tmp_path, "hunt.json", {"target_host": "a.invalid", "findings": {"sqli_bool_diff": "VERIFIED"}})
    state = load_file(tmp_path)  # type: ignore[arg-type]
    assert summarize(state)["total"] == 1
    assert summarize(state)["asset"] == "a.invalid"


def test_loader_prefers_hunt_over_coverage(tmp_path: object) -> None:
    _write(tmp_path, "hunt.json", {"target_host": "a.invalid", "findings": {}})
    _write(tmp_path, "coverage.json", {"asset_id": "b.invalid", "summary": {}, "gaps": []})
    assert summarize(load_file(tmp_path))["asset"] == "a.invalid"  # type: ignore[arg-type]


def test_coverage_only_artifact_takes_asset_from_ledger(tmp_path: object) -> None:
    _write(tmp_path, "coverage.json", {
        "asset_id": "c.invalid",
        "summary": {"faces": 1, "faces_covered": 0, "gaps": 1},
        "gaps": [{"kind": "face_without_oracle", "severity": "critical", "why": "x"}],
    })
    state = load_file(tmp_path)  # type: ignore[arg-type]
    assert summarize(state)["asset"] == "c.invalid", "只有账本时资产名别显示 unknown"
    assert state.coverage.get("ledger", {}).get("summary", {}).get("faces") == 1


def test_empty_directory_is_empty_state_not_a_crash(tmp_path: object) -> None:
    state = load_file(tmp_path)  # type: ignore[arg-type]
    assert state.findings == ()

# ── B145：hunt 的逐面记录才是权威，不许编 id / 证据 ─────────────
def _hunt(faces: list | None = None, findings: dict | None = None) -> dict:
    return {
        "target_host": "host",
        "findings": findings if findings is not None else {"csp_absent": "VERIFIED"},
        "not_confirmed": {},
        "faces_run": faces or [],
    }


def _face(oracle: str, verdict: str, tail: str, *, reason: str = "") -> dict:
    return {
        "oracle_id": oracle,
        "route_ref": "route://crawl/" + tail,
        "template": "https" + ":" + chr(0x2F) * 2 + "host" + chr(0x2F) + "x",
        "verdict": verdict,
        "reason": reason,
        "face_index": 1,
        "faces_total": 2,
    }


def test_faces_run_drives_ids_and_routes() -> None:
    from web.loader import load_record

    state = load_record(_hunt([_face("csp_absent", "VERIFIED", "aa" * 8, reason="csp_header_absent")]))
    row = state.findings[0]
    assert row.finding_id == "F-csp_absent-" + "aa" * 4, "id 要稳定（oracle + 面尾），不能是序号"
    assert row.route_ref == "route://crawl/" + "aa" * 8, "面里的真实 ref 不许丢"
    assert row.outcome == "reported"


def test_same_oracle_on_two_faces_gets_two_ids() -> None:
    """多面时同一 oracle 会出两条——按序号生成的 id 会撞。"""
    from web.loader import load_record

    state = load_record(_hunt([
        _face("csp_absent", "VERIFIED", "aa" * 8),
        _face("csp_absent", "VERIFIED", "bb" * 8),
    ]))
    ids = [r.finding_id for r in state.findings]
    assert len(set(ids)) == 2, ids


def test_no_fabricated_capsule_id() -> None:
    """没有证据列表时，控制台不许给出一个指向不存在 capsule 的 id。"""
    from web.loader import load_record

    state = load_record(_hunt([_face("csp_absent", "VERIFIED", "aa" * 8)]))
    assert state.findings[0].capsule_id is None


# ── B155：hunt 真的封了 capsule 之后，控制台要显示**那一面**的那份 ──
def _evidence(oracle: str, tail: str, cid: str) -> dict:
    return {"oracle": oracle, "route_ref": "route://crawl/" + tail, "capsule_id": cid}


def test_hunt_evidence_shows_up_on_the_right_face() -> None:
    """同一个 oracle 两个面各有一份：按面取，别按 oracle 取最后一份。

    按 oracle 建索引会让两条 finding 都显示后一份——"面 A 的发现引用面 B 的证据"，
    B148 在报告侧修过，这里在控制台侧再钉一次。
    """
    from web.loader import load_record

    record = _hunt(
        [_face("csp_absent", "VERIFIED", "aa" * 8), _face("csp_absent", "VERIFIED", "bb" * 8)],
    )
    record["evidence"] = [
        _evidence("csp_absent", "aa" * 8, "sha256:" + "1" * 64),
        _evidence("csp_absent", "bb" * 8, "sha256:" + "2" * 64),
    ]
    state = load_record(record)
    by_ref = {r.route_ref: r.capsule_id for r in state.findings}
    assert by_ref["route://crawl/" + "aa" * 8] == "sha256:" + "1" * 64
    assert by_ref["route://crawl/" + "bb" * 8] == "sha256:" + "2" * 64


def test_hunt_without_evidence_stays_none() -> None:
    """`evidence` 为空（老产物 / 全 BLOCKED）→ 仍然是 None，不编。"""
    from web.loader import load_record

    state = load_record(_hunt([_face("csp_absent", "VERIFIED", "aa" * 8)]))
    assert state.findings[0].capsule_id is None


def test_legacy_dict_still_loads_without_fabricated_evidence() -> None:
    """老产物没有 faces_run：仍能打开，但同样不许编证据指针。"""
    from web.loader import load_record

    state = load_record(_hunt([], findings={"csp_absent": "VERIFIED"}))
    assert state.findings and state.findings[0].capsule_id is None
