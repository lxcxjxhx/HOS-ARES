"""B70 · 成片清单判据（H15 判据 A–D）。

只测纯函数与产物结构：合成一次要跑 ffmpeg + Playwright，不适合进单测。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "demo" / "build_tour.py"
MANIFEST = REPO / "out" / "demo" / "tour-manifest.json"


def _module():
    spec = importlib.util.spec_from_file_location("build_tour", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _segments() -> list[dict[str, object]]:
    return [
        {"id": "intro", "kind": "card", "duration_s": 2.5},
        {"id": "ch-console", "kind": "card", "duration_s": 1.5},
        {"id": "console", "kind": "video", "duration_s": 27.4},
        {"id": "ch-terminal", "kind": "card", "duration_s": 1.5},
        {"id": "terminal", "kind": "video", "duration_s": 15.1},
        {"id": "outro", "kind": "card", "duration_s": 2.5},
    ]


def _good() -> dict[str, object]:
    return {
        "segments": _segments(),
        "expected_duration_s": 50.5,
        "video": {"duration_s": 50.5, "width": 1280, "height": 720},
    }


# ── A：规格 ────────────────────────────────────────────────────
def test_valid_manifest_passes() -> None:
    assert _module().validate_manifest(_good()) == []


def test_short_tour_detected() -> None:
    m = _good()
    m["video"] = {"duration_s": 20.0, "width": 1280, "height": 720}
    assert any("40s" in p for p in _module().validate_manifest(m))


def test_wrong_viewport_detected() -> None:
    m = _good()
    m["video"] = {"duration_s": 50.5, "width": 1920, "height": 1080}
    assert any("分辨率" in p for p in _module().validate_manifest(m))


# ── B：章节完整 ────────────────────────────────────────────────
def test_missing_segment_detected() -> None:
    m = _good()
    m["segments"] = [s for s in _segments() if s["id"] != "ch-terminal"]
    problems = _module().validate_manifest(m)
    assert any("ch-terminal" in p for p in problems)


# ── C：时长加和 ────────────────────────────────────────────────
def test_duration_mismatch_detected() -> None:
    m = _good()
    m["video"] = {"duration_s": 46.0, "width": 1280, "height": 720}
    assert any("1.5s" in p for p in _module().validate_manifest(m))


def test_duration_sums_to_expected() -> None:
    """六段之和必须等于 expected：卡片时长改了就得分对。"""
    m = _good()
    total = sum(float(s["duration_s"]) for s in m["segments"])
    assert abs(total - float(m["expected_duration_s"])) < 0.01


# ── 真实产物（有则验，无则说明）────────────────────────────────
def test_built_manifest_is_valid() -> None:
    if not MANIFEST.exists():
        pytest.skip("尚无 out/demo/tour-manifest.json：先跑 make demo")
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert _module().validate_manifest(data) == []
    ids = [s["id"] for s in data["segments"]]
    assert ids[0] == "intro" and ids[-1] == "outro", "成片必须带片头与片尾"
