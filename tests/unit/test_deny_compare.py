"""B314 · 边界对照脚本的取数与判定（纯函数部分）。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load():  # noqa: ANN202
    path = ROOT / "scripts" / "demo" / "deny_compare.py"
    spec = importlib.util.spec_from_file_location("deny_compare", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_summarize_counts_peer_in_deny() -> None:
    mod = _load()
    findings = {
        "counts": {"total": 2, "by_outcome": {"reported": 0, "not_run": 2}},
        "findings": [{"reason_code": "peer_in_deny"}, {"reason_code": "peer_in_deny"}],
    }
    row = mod.summarize(findings)
    assert row == {"total": 2, "verified": 0, "not_run": 2, "peer_in_deny": 2}


def test_render_row_has_all_four_numbers() -> None:
    mod = _load()
    text = mod.render_row("demo-a", {"total": 2, "verified": 2, "not_run": 0, "peer_in_deny": 0})
    assert "demo-a" in text and "2" in text


def test_both_profiles_are_referenced() -> None:
    mod = _load()
    assert "demo-a" in mod.ALLOW_PROFILE and "demo-deny" in mod.DENY_PROFILE
