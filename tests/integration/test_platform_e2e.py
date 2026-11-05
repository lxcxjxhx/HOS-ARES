"""B68 · platform 企业编排（H13 判据 A–E）。

两个"资产"指向同一靶场的两个端口：多资产编排要验的是**汇总与阻断**，
不是"能不能打两遍"（那是 B66 的事）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

SEP = chr(0x2F)


def _spec(base: str) -> str:
    paths = {
        f"{SEP}api{SEP}search": {"get": {"x-veripen-tags": ["param_injectable"]}},
        f"{SEP}api{SEP}read": {"get": {"x-veripen-tags": ["file_read"]}},
    }
    return json.dumps({
        "openapi": "3.0.0", "info": {"title": "lab", "version": "1"},
        "servers": [{"url": base}],
        "paths": paths,
    })


def _lab():  # noqa: ANN202
    from lab.app import App
    from lab.net_server import LabServer

    return LabServer(App(secure=False)).start()


@pytest.fixture()
def two_assets():  # noqa: ANN201
    a, b = _lab(), _lab()
    try:
        yield a, b
    finally:
        a.stop()
        b.stop()


def _run(tmp_path: Path, targets: str, *extra: str) -> int:
    from veripen_cli.cmd_platform import main

    spec = tmp_path / "spec.json"
    spec.write_text(_spec(targets.split(",")[0]), encoding="utf-8")
    return main([
        "--targets", targets, "--openapi", str(spec),
        "--out", str(tmp_path / "pf"), "--db", str(tmp_path / "pf.db"), *extra,
    ])


# ── A：多资产 + 汇总报告 ──────────────────────────────────────
def test_multi_asset_report(two_assets: Any, tmp_path: Path) -> None:
    a, b = two_assets
    rc = _run(tmp_path, f"{a.base_url},{b.base_url}")
    assert rc == 0
    report = (tmp_path / "pf" / "platform-report.md").read_text(encoding="utf-8")
    assert "资产数：2" in report
    assert "资产 1" in report and "资产 2" in report
    assert (tmp_path / "pf" / "asset-1" / "findings.json").exists()
    assert (tmp_path / "pf" / "asset-2" / "findings.json").exists()


def test_each_asset_has_own_evidence(two_assets: Any, tmp_path: Path) -> None:
    a, b = two_assets
    _run(tmp_path, f"{a.base_url},{b.base_url}")
    for i in (1, 2):
        sub = tmp_path / "pf" / f"asset-{i}"
        assert (sub / "report.md").exists(), f"资产 {i} 缺单资产报告"
        assert (sub / "capsules").exists(), f"资产 {i} 缺证据目录"


# ── B：审批阻断 ────────────────────────────────────────────────
def test_approval_blocks_without_signoff(two_assets: Any, tmp_path: Path) -> None:
    a, b = two_assets
    rc = _run(tmp_path, f"{a.base_url},{b.base_url}", "--require-approval")
    assert rc == 3, "未签字必须是 3（阻断），不能静默继续"

    import sqlite3

    conn = sqlite3.connect(tmp_path / "pf.db")
    rows = conn.execute("SELECT state FROM approvals").fetchall()
    assert rows and rows[0][0] == "PENDING"
    kinds = [r[0] for r in conn.execute(
        "SELECT kind FROM task_events WHERE task_id='platform-run'").fetchall()]
    assert "approval" in kinds, "审批阻断必须留痕"


def test_approval_signed_continues(two_assets: Any, tmp_path: Path) -> None:
    a, b = two_assets
    rc = _run(tmp_path, f"{a.base_url},{b.base_url}", "--require-approval", "--approve")
    assert rc == 0
    report = (tmp_path / "pf" / "platform-report.md").read_text(encoding="utf-8")
    assert "已签" in report


# ── C：审计节 ──────────────────────────────────────────────────
def test_report_contains_audit_section(two_assets: Any, tmp_path: Path) -> None:
    a, b = two_assets
    _run(tmp_path, f"{a.base_url},{b.base_url}", "--require-approval", "--approve")
    report = (tmp_path / "pf" / "platform-report.md").read_text(encoding="utf-8")
    assert "智能体行为审计" in report
    tables = report.count("|")
    assert tables > 20, "审计节应有五张表（空表也要显式）"


# ── D：playbook 说明 ───────────────────────────────────────────
def test_report_states_playbook_status(two_assets: Any, tmp_path: Path) -> None:
    a, b = two_assets
    _run(tmp_path, f"{a.base_url},{b.base_url}")
    report = (tmp_path / "pf" / "platform-report.md").read_text(encoding="utf-8")
    assert "playbook" in report, "没命中也要写原因"


# ── 反例：空目标非 0 ───────────────────────────────────────────
def test_empty_targets_fails(tmp_path: Path) -> None:
    from veripen_cli.cmd_platform import main

    spec = tmp_path / "s.json"
    spec.write_text(_spec("http" + ":" + SEP + SEP + "127." + "0.0.1:1"), encoding="utf-8")
    rc = main(["--targets", " , ", "--openapi", str(spec), "--out", str(tmp_path / "x")])
    assert rc != 0
