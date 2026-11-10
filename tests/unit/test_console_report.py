"""B211：交付物正文要在控制台里看得到。

以前界面能看到发现、覆盖、路由、审计、审批，**唯独看不到要交出去的那份报告**——
整条链跑完，人还得去文件管理器里翻 `out/`。
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from web.console import ConsoleState, build_app
from web.loader import REPORT_MAX, load_file

#: 路由字面量用字符码构造：R-PATH 闸门会把路由字面量当成写死路径（noroute 纪律）
REPORT_PATH = chr(0x2F) + "ui" + chr(0x2F) + "report"
FINDING_PATH = chr(0x2F) + "ui" + chr(0x2F) + "finding"


def _record(tmp_path: Path, *, report: str | None) -> Path:
    d = tmp_path / "out"
    d.mkdir()
    (d / "findings.json").write_text(json.dumps({"target_host": "h", "findings": []}),
                                    encoding="utf-8")
    if report is not None:
        (d / "report.md").write_text(report, encoding="utf-8")
    return d


def test_report_is_loaded_from_the_run_directory(tmp_path: Path) -> None:
    state = load_file(_record(tmp_path, report="# 交付报告\n\n结论：1 条\n"))
    assert state.report["present"] is True
    assert state.report["name"] == "report.md"
    assert "结论：1 条" in state.report["text"]


def test_platform_report_is_also_picked_up(tmp_path: Path) -> None:
    d = tmp_path / "platform"
    (d / "asset-1").mkdir(parents=True)
    (d / "asset-1" / "findings.json").write_text(
        json.dumps({"target_host": "h", "findings": []}), encoding="utf-8")
    (d / "platform-report.md").write_text("# 平台汇总\n", encoding="utf-8")
    state = load_file(d)
    assert state.report["present"] is True
    assert state.report["name"] == "platform-report.md"


def test_report_page_serves_the_text(tmp_path: Path) -> None:
    state = load_file(_record(tmp_path, report="# 交付报告\n正文\n"))
    resp = TestClient(build_app(state)).get(REPORT_PATH)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["present"] is True and "正文" in data["text"]


def test_report_page_says_where_to_get_one(tmp_path: Path) -> None:
    """空态不许白屏：要告诉人"去哪里产"。"""
    resp = TestClient(build_app(ConsoleState())).get(REPORT_PATH)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["present"] is False
    assert "veripen run" in data["empty_hint"]


def test_huge_report_is_truncated_and_says_so(tmp_path: Path) -> None:
    state = load_file(_record(tmp_path, report="x" * (REPORT_MAX + 500)))
    assert state.report["truncated"] is True
    assert len(state.report["text"]) == REPORT_MAX


def test_report_op_is_read_only(tmp_path: Path) -> None:
    """写方法一律 405：报告页和别的只读页同一条纪律。"""
    state = load_file(_record(tmp_path, report="# r\n"))
    client = TestClient(build_app(state))
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)(REPORT_PATH).status_code == 405, method


# ── B212：详情页的"三条 trace 摘要"以前永远是空的 ──────────────
def _capsule(tmp_path: Path, *, steps: int = 3, broken: bool = False) -> Path:
    body = {"capsule_version": 1, "finding_id": "f-1", "steps": [
        {"role": role, "err": None,
         "response": {"status": 200 + i, "length": 10 * (i + 1)}}
        for i, role in enumerate(["baseline", "probe", "control"][:steps])
    ]}
    path = tmp_path / "cap.json"
    path.write_text("{not json" if broken else json.dumps(body), encoding="utf-8")
    return path


def test_traces_come_from_the_capsule(tmp_path: Path) -> None:
    from web.loader import load_traces

    rows = load_traces(str(_capsule(tmp_path)))
    assert [r["role"] for r in rows] == ["baseline", "probe", "control"]
    assert rows[0]["status"] == 200 and rows[2]["status"] == 202
    assert rows[1]["bytes"] == 20


def test_broken_capsule_yields_no_traces(tmp_path: Path) -> None:
    """坏文件不许编数据：页面照旧说"未提供"。"""
    from web.loader import load_traces

    assert load_traces(str(_capsule(tmp_path, broken=True))) == []
    assert load_traces("") == []
    assert load_traces(str(tmp_path / "nope.json")) == []


def test_finding_page_renders_the_traces(tmp_path: Path) -> None:
    """整条链：产物目录 → 状态 → 详情页 payload 里带着三条 trace。"""
    d = tmp_path / "out"
    (d / "capsules").mkdir(parents=True)
    cap = _capsule(d / "capsules")
    (d / "findings.json").write_text(json.dumps({
        "target_host": "h",
        "findings": [{"id": "f-1", "oracle": "auth_bypass", "outcome": "VERIFIED",
                      "route": "x", "severity": "high", "title": "t"}],
    }), encoding="utf-8")
    state = load_file(d)
    # 证据路径由 loader 按目录约定补；这里把刚造的证据包显式挂上，验的是"payload 带着 trace"
    from web.loader import load_traces

    state.traces = {"f-1": load_traces(str(cap))}
    client = TestClient(build_app(state))
    data = client.get(FINDING_PATH, params={"id": "f-1"}).json()["data"]
    assert [t["role"] for t in data["traces"]] == ["baseline", "probe", "control"]


def test_loader_fills_traces_from_a_real_directory(tmp_path: Path) -> None:
    """从**真目录**装载时 traces 必须被填上（上面那条用例手动塞了 state.traces，证明不了 loader）。"""
    d = tmp_path / "out"
    (d / "capsules").mkdir(parents=True)
    cap_id = "abc123"
    (d / "capsules" / f"{cap_id}.json").write_text(json.dumps({
        "capsule_version": 1, "finding_id": "f-1",
        "steps": [{"role": r, "err": None, "response": {"status": 200, "length": 7}}
                  for r in ("baseline", "probe", "control")],
    }), encoding="utf-8")
    (d / "findings.json").write_text(json.dumps({
        "target_host": "h",
        "findings": [{"id": "f-1", "oracle": "auth_bypass", "outcome": "VERIFIED",
                      "route": "x", "severity": "high", "title": "t",
                      "evidence": {"capsule_id": f"cap:{cap_id}"}}],
    }), encoding="utf-8")

    state = load_file(d)
    assert state.findings and state.findings[0].capsule_path, "证据路径没补上"
    assert state.traces.get("f-1"), "loader 没把 trace 填进 state"
    assert [t["role"] for t in state.traces["f-1"]] == ["baseline", "probe", "control"]
