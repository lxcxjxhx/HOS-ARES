"""B13 doctor 测试（A3「内网能装」、13 §3「doctor 有任何红项不交付」）。

三条纪律，每条都有独立用例：
- **全 ok 才叫绿**：``skip`` 也算不绿——没检查过的不算通过；
- ``fail`` 必须给修复命令：否则就是"知道了但不改"；
- **单项崩溃不能拖垮整份报告**：doctor 自己不倒，人才有得查。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from veripen_cli.cmd_doctor import (
    CHECKS,
    CheckResult,
    DoctorReport,
    Status,
    check_layout,
    check_python,
    run_doctor,
)

ROOT = Path(__file__).resolve().parents[2]


# ══════════════════════════════════════════════════════════════════
# 1 状态语义
# ══════════════════════════════════════════════════════════════════
def test_green_requires_no_fail_and_no_skip() -> None:
    ok = CheckResult("a", Status.OK)
    skip = CheckResult("b", Status.SKIP, "环境不具备")
    fail = CheckResult("c", Status.FAIL, "红", "跑 xxx")

    assert DoctorReport([ok]).green is True
    assert DoctorReport([ok, skip]).green is False   # skip 不算通过
    assert DoctorReport([ok, fail]).green is False


def test_skip_is_not_counted_as_ok() -> None:
    """把「没检查」记成「通过」，是这类工具最常见的自欺。"""
    rep = DoctorReport([CheckResult("x", Status.SKIP)])
    assert rep.by_status() == {"ok": 0, "fail": 0, "skip": 1}
    assert rep.green is False


def test_counts_and_lists() -> None:
    rep = DoctorReport([
        CheckResult("a", Status.OK), CheckResult("b", Status.FAIL, "x", "fix"),
        CheckResult("c", Status.SKIP),
    ])
    assert [r.name for r in rep.failed] == ["b"]
    assert [r.name for r in rep.skipped] == ["c"]


def test_render_marks_every_status_and_shows_fix() -> None:
    rep = DoctorReport([
        CheckResult("a", Status.OK, "fine"),
        CheckResult("b", Status.FAIL, "坏", "跑 make doctor"),
        CheckResult("c", Status.SKIP),
    ])
    text = rep.render()
    assert "✅ a" in text and "❌ b" in text and "⏭ c" in text
    assert "修复：跑 make doctor" in text
    assert "不可交付" in text


def test_render_says_deliverable_only_when_green() -> None:
    text = DoctorReport([CheckResult("a", Status.OK)]).render()
    assert "全绿" in text and "不可交付" not in text


# ══════════════════════════════════════════════════════════════════
# 2 注册表
# ══════════════════════════════════════════════════════════════════
def test_registry_has_nineteen_checks() -> None:
    """B13 的 16 项 + B61 的 sandbox/tui/browser 三项。

    **加一项就要问：它能不能被别人独立重跑？**（`H11 B61`）
    """
    assert len(CHECKS) == 19
    names = [n for n, _ in CHECKS]
    assert len(set(names)) == 19


def test_every_check_takes_root() -> None:
    """签名统一成 (root)：否则注册表按名字遍历时会 TypeError（本项目真踩过）。"""
    for name, fn in CHECKS:
        assert fn.__code__.co_argcount == 1, name


def test_run_doctor_covers_all_or_selected() -> None:
    rep = run_doctor(ROOT, only=["python", "layout"])
    assert [r.name for r in rep.results] == ["python 版本", "目录布局"]


def test_unknown_check_name_is_ignored_not_crash() -> None:
    rep = run_doctor(ROOT, only=["no_such_check"])
    assert rep.results == []


# ══════════════════════════════════════════════════════════════════
# 3 单项行为
# ══════════════════════════════════════════════════════════════════
def test_python_version_check_passes_here() -> None:
    assert check_python(ROOT).status is Status.OK


def test_layout_check_detects_missing_dirs(tmp_path: Path) -> None:
    r = check_layout(tmp_path)
    assert r.status is Status.FAIL
    assert "缺" in r.detail
    assert r.fix  # fail 必须给修复建议


def test_layout_passes_on_repo() -> None:
    assert check_layout(ROOT).status is Status.OK


# ══════════════════════════════════════════════════════════════════
# 4 崩溃隔离
# ══════════════════════════════════════════════════════════════════
def test_crashing_check_does_not_break_the_report(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(root: Path) -> CheckResult:
        raise RuntimeError("检查自己炸了")

    monkeypatch.setattr("veripen_cli.cmd_doctor.CHECKS",
                        (("boom", boom), ("python", check_python)))
    rep = run_doctor(ROOT)
    assert len(rep.results) == 2
    assert rep.results[0].status is Status.FAIL
    assert "检查自身抛错" in rep.results[0].detail
    assert rep.results[1].status is Status.OK  # 后一项照跑


# ══════════════════════════════════════════════════════════════════
# 5 整仓跑一遍
# ══════════════════════════════════════════════════════════════════
def test_repo_doctor_has_no_failures() -> None:
    """本仓当前状态：**不许有 fail**。skip 允许（docker 可能没有）。"""
    rep = run_doctor(ROOT)
    assert rep.failed == [], [r.as_dict() for r in rep.failed]


def test_repo_doctor_json_is_serialisable() -> None:
    rep = run_doctor(ROOT, only=["python", "layout"])
    blob = json.dumps({"results": [r.as_dict() for r in rep.results]}, ensure_ascii=False)
    assert "python" in blob

# ── B105：浏览器实测是显式开关，默认不做 ─────────────────────────
def test_browser_check_defaults_to_no_launch(monkeypatch) -> None:
    from pathlib import Path

    from veripen_cli.cmd_doctor import check_browser

    monkeypatch.delenv("VERIPEN_DOCTOR_BROWSER", raising=False)
    result = check_browser(Path("."))
    # 没装 playwright 时是 skip；装了就是 ok——两种都不许"起浏览器"
    assert result.status in {"ok", "skip"}
    assert "launch" in result.detail or result.status == "skip"
