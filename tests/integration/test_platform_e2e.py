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


# ── 无契约资产：--discover（B162）──────────────────────────────
_LOCAL = "127" + "." + "0" + "." + "0" + "." + "1"
_HOME = (
    '<html><body><a href="' + SEP + 'a?x=1">a</a>'
    '<a href="' + SEP + 'b?x=1">b</a></body></html>'
).encode()


def _page_server():  # noqa: ANN202
    """最小 Web：`/` 是带两个入口的 HTML，`/a` `/b` 返回 JSON。"""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body, ctype = b'{"total":0,"items":[]}', "application/json"
            if not (self.path.startswith(SEP + "a") or self.path.startswith(SEP + "b")):
                body, ctype = _HOME, "text/html"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):  # noqa: ANN002
            pass

    srv = HTTPServer((_LOCAL, 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http" + ":" + SEP * 2 + f"{_LOCAL}:{srv.server_address[1]}"


def test_platform_can_run_without_a_contract(tmp_path: Path) -> None:
    """企业现场经常没有契约：平台要能像 `run` 一样靠探测跑，并写明路由来源。"""
    from veripen_cli.cmd_platform import main

    srv, base = _page_server()
    try:
        rc = main([
            "--targets", base, "--discover",
            "--discover-rounds", "1", "--discover-pages", "4",
            "--out", str(tmp_path / "pf"), "--db", str(tmp_path / "pf.db"),
        ])
    finally:
        srv.shutdown()
    assert rc == 0, "无契约跑不通：平台没跟上 run 的探测能力"
    report = (tmp_path / "pf" / "platform-report.md").read_text(encoding="utf-8")
    assert "主动探测（无契约" in report, "读者必须知道这批路由是探测来的"
    assert "已验证 **" in report
    assert "| 1 |" in report and "| — | — | — |" not in report, "这一轮真的产出了"


def test_contract_and_discover_are_exclusive(tmp_path: Path) -> None:
    from veripen_cli.cmd_platform import main

    spec = tmp_path / "spec.json"
    spec.write_text(_spec("http" + ":" + SEP * 2 + "127." + "0.0.1:1"), encoding="utf-8")
    rc = main(["--targets", "http" + ":" + SEP * 2 + "127." + "0.0.1:1",
               "--openapi", str(spec), "--discover",
               "--out", str(tmp_path / "x"), "--db", str(tmp_path / "x.db")])
    assert rc == 2, "两个路由来源同时给：不静默取一个"


def test_platform_requires_a_route_source(tmp_path: Path) -> None:
    from veripen_cli.cmd_platform import main

    rc = main(["--targets", "http" + ":" + SEP * 2 + "127." + "0.0.1:1",
               "--out", str(tmp_path / "x"), "--db", str(tmp_path / "x.db")])
    assert rc == 2, "既没契约也没探测：不许瞎猜面"


# ── G：凭据透传，且不落报告（B163）──────────────────────────────
_TARGET = "http" + ":" + SEP * 2 + "127." + "0.0.1:1"


def _capture(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    from veripen_cli import cmd_run

    seen: list[list[str]] = []

    def fake_run(argv: list[str]) -> int:
        seen.append(list(argv))
        return 1  # 走"未产出"分支：不写 findings，报告仍会生成

    monkeypatch.setattr(cmd_run, "main", fake_run)
    return seen


def test_token_reaches_each_asset_and_never_enters_the_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """平台原来不传 token：带鉴权的资产在平台里永远只能落 BLOCKED。"""
    from veripen_cli.cmd_platform import main

    seen = _capture(monkeypatch)
    spec = tmp_path / "spec.json"
    spec.write_text(_spec(_TARGET), encoding="utf-8")
    rc = main(["--targets", _TARGET, "--openapi", str(spec), "--token", "SECRET-TOKEN",
               "--out", str(tmp_path / "pf"), "--db", str(tmp_path / "pf.db")])
    assert rc == 1
    assert seen, "逐资产链路没被调用"
    assert ["--token", "SECRET-TOKEN"] == seen[0][seen[0].index("--token"):][:2]

    report = (tmp_path / "pf" / "platform-report.md").read_text(encoding="utf-8")
    assert "主身份 token 已注入" in report
    assert "SECRET-TOKEN" not in report, "报告是要发给客户的：凭据原文不许进报告"


def test_report_says_when_no_credential(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from veripen_cli.cmd_platform import main

    _capture(monkeypatch)
    spec = tmp_path / "spec.json"
    spec.write_text(_spec(_TARGET), encoding="utf-8")
    main(["--targets", _TARGET, "--openapi", str(spec),
          "--out", str(tmp_path / "pf"), "--db", str(tmp_path / "pf.db")])
    report = (tmp_path / "pf" / "platform-report.md").read_text(encoding="utf-8")
    assert "凭据：未提供" in report, "没凭据要说出来，否则读者不知道为什么一堆 BLOCKED"


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


# ── F：重跑被拒时，不许把上一轮的 findings 当成这一轮的（B159）──
def test_rerun_does_not_reuse_stale_findings(two_assets: Any, tmp_path: Path) -> None:
    """同一 out 再跑一次：cmd_run 会拒跑（证据目录非空），平台必须如实报"未产出"。

    修前实测：第二次每个资产都 exit=2，报告却照样写"已验证 2 / 总项 4"，
    rc 那列说 2、结论列说 2——自相矛盾，且把一轮没发生的事算成了结果。
    """
    a, _b = two_assets
    assert _run(tmp_path, a.base_url) == 0
    first = (tmp_path / "pf" / "platform-report.md").read_text(encoding="utf-8")
    assert "已验证 **2**" in first, "第一次应当真的跑出结论"

    assert _run(tmp_path, a.base_url) == 1, "重跑被拒 -> 平台退出码非 0"
    second = (tmp_path / "pf" / "platform-report.md").read_text(encoding="utf-8")
    assert "本轮未产出 1 个" in second
    assert "已验证 **0** / 总项 0" in second, "旧结论不许被算进这一轮"
    assert "| — | — | — |" in second, "未产出的资产三列都必须是 —，不能填数"
    assert "目录里的旧产物未被采用" in second
