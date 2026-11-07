"""B137 · doctor 不能因为"测试夹具不在"就崩。

三条检查（routeset / capsule / replay-offline）都直接 import `tests/` 下的夹具。
装在正式环境（没有 tests/）时，它们以前会抛 ImportError 把 doctor 打挂——
老账 B13 记的就是这件事。

正确行为：**WARN + 说清"本条未验"**。既不是崩，也不是假装通过。
"""

from __future__ import annotations

import builtins

import pytest


def _block(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    real = builtins.__import__

    def _guard(mod: str, *a: object, **kw: object):  # noqa: ANN202
        if mod == name or mod.startswith(name + "."):
            raise ModuleNotFoundError(f"No module named {name!r}")
        return real(mod, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _guard)


def test_routes_check_warns_when_fixture_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    from veripen.doctor import _check_routes_deterministic

    _block(monkeypatch, "lab.routes")
    check = _check_routes_deterministic()
    assert check.level.name == "WARN", check.detail
    assert "未验" in check.detail
    assert check.fix, "WARN 也要说清怎么办"


def test_capsule_check_warns_when_fixture_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    from veripen.doctor import _check_capsule_roundtrip

    _block(monkeypatch, "capsule_fixture")
    check = _check_capsule_roundtrip()
    assert check.level.name == "WARN", check.detail
    assert "未验" in check.detail


def test_replay_check_warns_when_fixture_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    from veripen.doctor import _check_replay_offline

    _block(monkeypatch, "capsule_fixture")
    check = _check_replay_offline()
    assert check.level.name == "WARN", check.detail
    assert "未验" in check.detail


def test_doctor_has_no_failures_even_without_fixtures(monkeypatch: pytest.MonkeyPatch) -> None:
    """三条一起缺 → 仍然 0 FAIL（WARN 不算失败，但要显式出现）。"""
    from veripen.doctor import run_all

    _block(monkeypatch, "capsule_fixture")
    _block(monkeypatch, "lab")
    report = run_all()
    assert not report.failures, [c.line() for c in report.failures]
    warned = {c.name for c in report.checks if c.level.name == "WARN"}
    assert {"routeset", "capsule", "replay-offline"} <= warned, warned
