"""B65 · 控制台巡检录屏的清单判据（H12 判据 A–D）。

只测纯函数与产物结构，不启动浏览器：录屏本身跑一次 20s+，
不适合放进单测。真实录制由 ``scripts/demo/record_console.py`` 手动触发。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "demo" / "record_console.py"
MANIFEST = REPO / "out" / "demo" / "manifest.json"


def _module():
    spec = importlib.util.spec_from_file_location("record_console", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ops() -> list[str]:
    from web.console import UI_OPS

    return [op.split(".", 1)[-1] for op in UI_OPS]


def _shot(op: str, idx: int) -> dict[str, object]:
    return {"op": op, "path": "#" + op, "png": f"pages/{idx:02d}-{op}.png",
            "duration_s": 2.5, "bytes": 1024}


# ── 清单构造 ───────────────────────────────────────────────────
def test_manifest_carries_page_order_and_counts() -> None:
    mod = _module()
    ops = _ops()
    shots = [_shot(op, i) for i, op in enumerate(ops, start=1)]
    video = {"path": "console-tour.mp4", "duration_s": 25.0,
             "width": 1280, "height": 720, "fps": 30, "bytes": 1}
    m = mod.build_manifest(shots, video, {"findings": 3, "events": 11, "approvals": 4})
    assert m["page_order"] == ops
    assert m["counts"]["events"] == 11
    assert len(m["pages"]) == len(ops)


# ── A/B/C：规格与数据 ──────────────────────────────────────────
def test_valid_manifest_passes() -> None:
    mod = _module()
    ops = _ops()
    shots = [_shot(op, i) for i, op in enumerate(ops, start=1)]
    video = {"duration_s": 25.0, "width": 1280, "height": 720}
    m = mod.build_manifest(shots, video, {"findings": 1, "events": 1, "approvals": 1})
    assert mod.validate_manifest(m, ops) == []


def test_short_video_detected() -> None:
    mod = _module()
    ops = _ops()
    shots = [_shot(op, i) for i, op in enumerate(ops, start=1)]
    video = {"duration_s": 4.0, "width": 1280, "height": 720}
    m = mod.build_manifest(shots, video, {"findings": 1, "events": 1, "approvals": 1})
    problems = mod.validate_manifest(m, ops)
    assert any("25s" in p for p in problems)


def test_empty_counts_detected() -> None:
    mod = _module()
    ops = _ops()
    shots = [_shot(op, i) for i, op in enumerate(ops, start=1)]
    video = {"duration_s": 25.0, "width": 1280, "height": 720}
    m = mod.build_manifest(shots, video, {"findings": 0, "events": 0, "approvals": 0})
    problems = mod.validate_manifest(m, ops)
    assert any("findings" in p for p in problems)
    assert any("events" in p for p in problems)


def test_wrong_page_order_detected() -> None:
    mod = _module()
    ops = _ops()
    shots = [_shot(op, i) for i, op in enumerate(reversed(ops), start=1)]
    video = {"duration_s": 25.0, "width": 1280, "height": 720}
    m = mod.build_manifest(shots, video, {"findings": 1, "events": 1, "approvals": 1})
    assert any("页序列" in p for p in mod.validate_manifest(m, ops))


def test_wrong_viewport_detected() -> None:
    mod = _module()
    ops = _ops()
    shots = [_shot(op, i) for i, op in enumerate(ops, start=1)]
    video = {"duration_s": 25.0, "width": 800, "height": 600}
    m = mod.build_manifest(shots, video, {"findings": 1, "events": 1, "approvals": 1})
    assert any("分辨率" in p for p in mod.validate_manifest(m, ops))


# ── 真实产物（有则验，无则说明）────────────────────────────────
def test_committed_demo_manifest_is_valid() -> None:
    if not MANIFEST.exists():
        pytest.skip("尚无 out/demo/manifest.json：先跑 scripts/demo/record_console.py")
    mod = _module()
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert mod.validate_manifest(data, _ops()) == []


# ── B211：录屏是快照，不是"永远等于产品" ────────────────────────
def test_manifest_may_lag_behind_new_pages() -> None:
    """产品多一个页签，旧录像不该被判错——否则每加一页都得重录一遍。"""
    mod = _module()
    ops = ["overview", "engagement", "findings", "report"]
    data = {"page_order": ["overview", "engagement"],
            "recorded_op_count": 2,
            "video": {"duration_s": 30, "width": mod.VIEWPORT_W, "height": mod.VIEWPORT_H},
            "counts": {"findings": 3, "events": 4, "approvals": 1}}
    assert mod.validate_manifest(data, ops) == []


def test_manifest_rejects_pages_that_no_longer_exist() -> None:
    mod = _module()
    ops = ["overview", "engagement"]
    data = {"page_order": ["overview", "ghost"],
            "recorded_op_count": 2,
            "video": {"duration_s": 30, "width": mod.VIEWPORT_W, "height": mod.VIEWPORT_H},
            "counts": {"findings": 3, "events": 4, "approvals": 1}}
    problems = mod.validate_manifest(data, ops)
    assert any("已经不存在的页" in p for p in problems), problems


def test_manifest_rejects_out_of_order_pages() -> None:
    mod = _module()
    ops = ["overview", "engagement", "findings"]
    data = {"page_order": ["findings", "overview"],
            "recorded_op_count": 2,
            "video": {"duration_s": 30, "width": mod.VIEWPORT_W, "height": mod.VIEWPORT_H},
            "counts": {"findings": 3, "events": 4, "approvals": 1}}
    problems = mod.validate_manifest(data, ops)
    assert any("顺序不一致" in p for p in problems), problems
