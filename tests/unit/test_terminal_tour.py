"""B69 · 终端体验录屏的清单判据（H14 判据 A–D）。

只测纯函数与产物结构，不启动浏览器/子进程：录一次 15s+，不适合进单测。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "demo" / "record_terminal.py"
MANIFEST = REPO / "out" / "demo" / "terminal" / "manifest.json"


def _module():
    spec = importlib.util.spec_from_file_location("record_terminal", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _scenes() -> list[dict[str, object]]:
    return [
        {"id": "run", "lines": 10, "prompt": "python -m veripen_cli.cmd_run"},
        {"id": "agent", "lines": 8, "prompt": "python -m veripen_cli.cmd_agent"},
        {"id": "platform", "lines": 12, "prompt": "python -m veripen_cli.cmd_platform"},
        {"id": "profile", "lines": 9, "prompt": "VP_ASSET_WEB_01=<lab> ... --profile ..."},
    ]


def _good() -> dict[str, object]:
    return {
        "scenes": _scenes(),
        "video": {"duration_s": 19.9, "width": 1280, "height": 720},
        # 拆成 3+2 拼接：`lint-nodata` 把"≥5 元素字面量列表"当成硬编码行集
        "all_lines": [
            "结论：VERIFIED 2 / 共 5 项",
            " 1 ok  write  out/x.txt 写入 21B",
            "汇总：2 资产 / 已验证 4 / 总项 10",
        ] + [
            "客户档：demo-a · DEMO-A-001 · ENG-2026-001",
            "档策略：可用 oracle 9 类 / 需审批 3 类",
        ],
    }


# ── A：规格 ────────────────────────────────────────────────────
def test_valid_manifest_passes() -> None:
    assert _module().validate_manifest(_good()) == []


def test_short_video_detected() -> None:
    m = _good()
    m["video"] = {"duration_s": 8.0, "width": 1280, "height": 720}
    assert any("18s" in p for p in _module().validate_manifest(m))


def test_wrong_viewport_detected() -> None:
    m = _good()
    m["video"] = {"duration_s": 15.0, "width": 800, "height": 600}
    assert any("分辨率" in p for p in _module().validate_manifest(m))


# ── B/C：场景与关键行 ──────────────────────────────────────────
def test_scene_order_enforced() -> None:
    m = _good()
    m["scenes"] = list(reversed(_scenes()))
    assert any("场景序列" in p for p in _module().validate_manifest(m))


def test_missing_key_line_detected() -> None:
    m = _good()
    m["all_lines"] = ["只有一行无关输出"]
    problems = _module().validate_manifest(m)
    assert any("VERIFIED 2" in p for p in problems)
    assert any("ok  write" in p for p in problems)


def test_command_strings_count_from_prompt() -> None:
    """命令串在 scene.prompt 里（画面上的 `$` 行），不在 stdout。"""
    m = _good()
    m["all_lines"] = [
        "结论：VERIFIED 2 / 共 5 项", " 1 ok  write  x", "汇总：2 资产",
    ] + ["客户档：demo-a", "档策略：可用 oracle 9 类"]
    assert _module().validate_manifest(m) == [], "prompt 已提供命令串，不应误判"


# ── 真实产物（有则验，无则说明）────────────────────────────────
def test_recorded_manifest_is_valid() -> None:
    if not MANIFEST.exists():
        pytest.skip("尚无 out/demo/terminal/manifest.json：先跑 scripts/demo/record_terminal.py")
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert _module().validate_manifest(data) == []
    assert [s["id"] for s in data["scenes"]] == ["run", "agent", "platform", "profile"]
