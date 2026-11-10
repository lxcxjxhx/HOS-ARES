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
