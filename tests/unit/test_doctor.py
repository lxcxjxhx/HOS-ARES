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

from veripen_cli import cmd_doctor
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
def test_registry_checks_are_unique_and_signed() -> None:
    """注册表的不变量：名字唯一、签名统一、条数只增不减。

    以前写死 `== 19`，每加一条检查都要回来改（B127 加"执行体行为"时又红一次）。
    数字会过时，不变量不会——这里下限用 19，是"已有能力不能悄悄消失"。
    """
    names = [n for n, _ in CHECKS]
    assert len(names) == len(set(names)), "检查名重复"
    assert len(CHECKS) >= 19, f"检查条数变少了：{len(CHECKS)}"


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


# ── B200：探针没跑起来 ≠ 行为坏 ──────────────────────────────
def test_executor_check_splits_unproven_env_from_broken_behaviour(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    """干净 runner 上子进程既读不到 stdout 也拿不到退出码：记 skip，不记 fail。"""
    from veripen_cli import cmd_doctor

    def forged(**facts):  # noqa: ANN003, ANN202
        def fake_attest(executor):  # noqa: ANN001, ANN202
            return {"executor": "local", "available": True, **facts}

        def fake_verdict(_facts):  # noqa: ANN001, ANN202
            return False, "行为不完整"

        monkeypatch.setattr("veripen_core.sandbox.attest.attest", fake_attest)
        monkeypatch.setattr("veripen_core.sandbox.attest.verdict", fake_verdict)
        monkeypatch.setattr("veripen_core.sandbox.runner.select_executor",
                            lambda: type("E", (), {"name": "local", "available": lambda self: True})())
        return cmd_doctor.check_sandbox_behavior(tmp_path)

    never_ran = forged(stdout_captured=False, exit_code_preserved=False, timeout_enforced=True)
    assert never_ran.status is cmd_doctor.Status.SKIP, never_ran.status
    assert "没能执行" in never_ran.detail

    truly_broken = forged(stdout_captured=True, exit_code_preserved=False, timeout_enforced=True)
    assert truly_broken.status is cmd_doctor.Status.FAIL


# ── B209：doctor 的 lint 覆盖必须跟上 verify 的清单 ──────────────
def test_doctor_covers_every_gate_in_the_list(tmp_path) -> None:  # noqa: ANN001
    """`scripts/verify.py` 里每一道闸门，doctor 要么跑、要么写明为什么不跑。"""
    root = Path(__file__).resolve().parents[2]
    result = cmd_doctor.check_gate_parity(root)
    assert result.status is cmd_doctor.Status.OK, result.detail
    assert "道跑" in result.detail and "写明不跑" in result.detail, result.detail


def test_every_exclusion_carries_a_reason() -> None:
    """`DOCTOR_EXCLUDED` 不是逃生门：每条都要有理由，否则就是悄悄绕过。"""
    assert cmd_doctor.DOCTOR_EXCLUDED, "排除表空了——要么真有排除，要么这条用例该删"
    for name, reason in cmd_doctor.DOCTOR_EXCLUDED.items():
        assert reason.strip(), f"{name} 没写理由"


def test_doctor_flags_a_missing_lint(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """把 noleak 从 doctor 源码里抹掉，这条检查必须红——否则它只是墙上的标语。"""
    import shutil

    root = Path(__file__).resolve().parents[2]
    copy = tmp_path / "repo"
    copy.mkdir()
    (copy / "scripts").mkdir()
    shutil.copy2(root / "scripts" / "verify.py", copy / "scripts" / "verify.py")

    # 用一份"漏了 noleak"的 doctor 源码替代当前实现
    altered = tmp_path / "cmd_doctor_copy.py"
    altered.write_text((root / "veripen_cli" / "cmd_doctor.py").read_text(encoding="utf-8")
                    .replace('"noleak.py"', '"__gone__.py"'), encoding="utf-8")
    monkeypatch.setattr(cmd_doctor, "__file__", str(altered))
    result = cmd_doctor.check_gate_parity(copy)
    assert result.status is cmd_doctor.Status.FAIL, result.detail
    assert "noleak.py" in result.detail, result.detail


# ── B216：闸门"写了"不等于"接上了" ─────────────────────────────
def test_agent_guard_check_passes_on_the_repo() -> None:
    root = Path(__file__).resolve().parents[2]
    result = cmd_doctor.check_agent_guard(root)
    assert result.status is cmd_doctor.Status.OK, result.detail
    assert "两个入口已接" in result.detail, result.detail


def test_agent_guard_check_catches_an_unwired_executor(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """闸门从**执行路径**上摘掉，这项必须红——grep 到 import 就绿是最容易犯的假绿。"""
    import veripen_cli.cmd_chat as chat

    def _unwired(step, *, wb, root, allow_dangerous=False):  # noqa: ANN001, ANN202
        return True, "exit=0（闸门被摘掉）"

    monkeypatch.setattr(chat, "_exec_step", _unwired)
    root = Path(__file__).resolve().parents[2]
    result = cmd_doctor.check_agent_guard(root)
    assert result.status is cmd_doctor.Status.FAIL, result.detail
    assert "没拦" in result.detail, result.detail
