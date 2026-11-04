"""B61 · doctor 三项运行时探测（H11 判据 A–E）。

核心纪律：**缺条件记 skip，不记 fail**。doctor 说"环境坏了"而 parity 说
"只是没验"，是两个口径分叉，人不知道该信谁。
"""

from __future__ import annotations

import builtins
import json
import sys
from pathlib import Path

import pytest

from veripen_cli import cmd_doctor
from veripen_cli.cmd_doctor import Status, run_doctor

ROOT = Path(__file__).resolve().parents[2]


# ── A：无运行时 = skip，且不产 fail ─────────────────────────────
def test_sandbox_skip_without_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("veripen_core.sandbox.runner.select_executor", lambda: None)
    r = cmd_doctor.check_sandbox(ROOT)
    assert r.status is Status.SKIP
    assert "BLOCKED" in r.detail
    assert r.fix, "skip 也要给可执行建议"


def test_sandbox_ok_with_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeExecutor:
        pass

    monkeypatch.setattr(
        "veripen_core.sandbox.runner.select_executor", lambda: FakeExecutor()
    )
    r = cmd_doctor.check_sandbox(ROOT)
    assert r.status is Status.OK
    assert "FakeExecutor" in r.detail


# ── C/D：缺依赖 = skip ─────────────────────────────────────────
def test_tui_skip_without_curses(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *a: object, **kw: object):
        if name == "curses":
            raise ImportError("no curses")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.delitem(sys.modules, "curses", raising=False)
    r = cmd_doctor.check_tui(ROOT)
    assert r.status is Status.SKIP
    assert "curses" in r.detail


def test_browser_skip_without_playwright(monkeypatch: pytest.MonkeyPatch) -> None:
    from veripen_core.render.browser import BrowserRenderer

    monkeypatch.setattr(BrowserRenderer, "available", lambda self: False)
    r = cmd_doctor.check_browser(ROOT)
    assert r.status is Status.SKIP
    assert "blocked" in r.detail


# ── E：三项进报告、by_status 计数正确 ──────────────────────────
def test_checks_registered_and_counted() -> None:
    names = [n for n, _ in cmd_doctor.CHECKS]
    for want in ("sandbox", "tui", "browser"):
        assert want in names, f"{want} 未注册进 CHECKS"

    report = run_doctor(ROOT, only=["sandbox", "tui", "browser"])
    got = {r.name for r in report.results}
    assert got == {"沙箱执行体", "TUI", "浏览器渲染"}
    counts = report.by_status()
    assert sum(counts.values()) == 3
    assert counts["fail"] == 0, f"本机不应有 fail：{counts}"


# ── B61 附带修的两个 CLI bug ───────────────────────────────────
def test_json_flag_reaches_argparse(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """``python -m`` 调用时 argv 为 None，原实现传空列表 → --json 被静默忽略。"""
    monkeypatch.setattr(sys, "argv", ["cmd_doctor", "--json", "--only", "tui"])
    cmd_doctor.main()  # 不传 argv，走 sys.argv
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert len(payload["results"]) == 1
    assert payload["results"][0]["name"] == "TUI"


def test_green_stays_strict_on_skip() -> None:
    """skip 不算绿是既有纪律，不为新探测放松（判据 A 的措辞据此修正）。"""
    report = run_doctor(ROOT, only=["sandbox"])
    if report.results[0].status is Status.SKIP:
        assert report.green is False
