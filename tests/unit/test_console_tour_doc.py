"""B269 · 控制台录屏也要能跑、能重跑，文档数字要跟得上。

两条与终端录屏（B267/B268）同一个坑，这次一并处理：
1. `pw.chromium.launch()` 写死——本机自带 chromium 起不来，整条链直接不可用；
2. 判据 D（两次运行页序列一致）原本靠手动，且脚本每次覆盖 `out/demo/manifest.json`。

改前文档写"10 个只读页 / 27.4s / 10 张截图"，真跑是 **11 页 / 30.03s / 11 张**
（新增了 `report` 页）。
"""

from __future__ import annotations

import re

import importlib.util
from pathlib import Path
from script_module import load_script_module  # noqa: E402
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "demo" / "record_console.py"
DOC = ROOT / "docs" / "delivery" / "console-tour.md"


def _mod() -> Any:
    # 用共用函数（B587）。这处原来是 `module_from_spec(spec)` 在 `assert` **之前**
    # —— 断言使用后再写等于没写（B580/B582/B585 同形）。
    mod: Any = load_script_module("record_console", SCRIPT)
    return mod


def test_browser_launch_falls_back_to_a_system_browser() -> None:
    src = SCRIPT.read_text(encoding="utf-8")
    # 不钉签名文本：B574 补类型标注后 `"def _launch(pw)"` 不存在了（B575 修）。
    assert re.search(r"def _launch\(", src, re.M), "起浏览器的兜底函数不见了"
    assert 'channel=channel' in src, "没有退到系统浏览器，本机自带 chromium 起不来就整条链不可用"
    for channel in ("msedge", "chrome"):
        assert f'"{channel}"' in src, channel


def test_repeat_run_comparison_detects_page_drift() -> None:
    compare = _mod()._compare_runs
    prev = {"generated_by": "t0", "page_order": ["overview", "findings"]}
    same = {"page_order": ["overview", "findings"]}
    drift = {"page_order": ["overview", "audit"]}
    assert compare(prev, same)["same_pages"] is True
    assert compare(prev, drift)["same_pages"] is False
    first = compare(None, same)
    assert first["checked"] is False and "基线" in first["reason"]


def test_doc_matches_the_real_page_count_and_duration() -> None:
    doc = DOC.read_text(encoding="utf-8")
    assert "11 个只读页" in doc, "页数与实测不符"
    assert "30.0s" in doc
    assert "11 张逐页截图" in doc
    for stale in ("10 个只读页", "27.4s", "10 张逐页截图"):
        assert stale not in doc, f"还留着旧数字：{stale}"


def test_doc_no_longer_says_judgement_d_is_manual() -> None:
    doc = DOC.read_text(encoding="utf-8")
    assert "D 需两次运行比对 manifest" not in doc
    assert "自动：脚本读上一轮 manifest 比对" in doc
