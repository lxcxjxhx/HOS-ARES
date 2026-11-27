"""B270 · 总片：第三处写死的浏览器、以及**片上文案**的过期数字。

两个发现：
1. `build_tour.py` 里也是 `pw.chromium.launch()`——同一行代码在三个录屏脚本里各写一遍，
   就三处一起坏（B267 终端、B269 控制台、本轮总片）。
2. 更糟的是**片上文案**：章节卡写死 `10 个只读页` / `三幕终端`，而素材早已是 11 页、五幕。
   文档能改，画面里烧进去的字不会自己改——这是"改文档不等于改产物"。

本用例只做静态对齐（不跑浏览器）：卡片文案不许再写旧的页数/幕数，文档数字要与实测一致。
"""

from __future__ import annotations

import re

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "demo" / "build_tour.py"
DOC = ROOT / "docs" / "delivery" / "tour.md"
MANIFEST = ROOT / "out" / "demo" / "tour-manifest.json"


def test_tour_script_falls_back_to_a_system_browser() -> None:
    src = SCRIPT.read_text(encoding="utf-8")
    # 只验"有这个函数"，不钉签名文本：B574 给它补类型标注（`pw: Any`）之后
    # `"def _launch(pw)"` 这个字面量就不存在了（B575 修）。
    assert re.search(r"def _launch\(", src), "起浏览器的兜底函数不见了"
    assert "channel=channel" in src
    assert 'pw.chromium.launch()' in src, "自带 chromium 仍是首选，只是不再写死"


def test_card_text_does_not_pin_stale_counts() -> None:
    src = SCRIPT.read_text(encoding="utf-8")
    for stale in ('"10 个只读页"', '"三幕终端"'):
        assert stale not in src, f"片上文案还写着手写数字：{stale}"
    # B271 起这些数字由 _counts() 从素材 manifest 读，不再出现在源码字面量里
    assert "_counts()" in src


def test_doc_segment_table_matches_the_built_manifest() -> None:
    """文档里的分段时长必须等于真出片的分段时长——否则判据 C 按文档算是过不了的。"""
    if not MANIFEST.exists():          # 没跑过成片就跳过（out/ 不入库）
        return
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    doc = DOC.read_text(encoding="utf-8")
    for seg in manifest["segments"]:
        assert f"| {seg['duration_s']}s |" in doc or f"| {seg['duration_s']:.1f}s |" in doc, (
            f"文档里找不到 {seg['id']} 的时长 {seg['duration_s']}s")
    total = manifest["video"]["duration_s"]
    assert f"{total}s" in doc, f"文档没写实测总长 {total}s"
    assert "50.5s" not in doc, "还留着旧的总长"


# ── B271：卡片文案按素材实测推导，不再手写 ──────────────────────
def test_card_counts_are_derived_from_the_material_manifests() -> None:
    """素材长到 11 页/五幕后，片上还烧着 10 页/三幕——文档改了也改不了画面里的字。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_tour", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    pages, scenes = mod._counts()
    assert pages is None or pages >= 1
    assert scenes is None or scenes >= 1
    src = SCRIPT.read_text(encoding="utf-8")
    assert '"11 个只读页"' not in src and '"五幕终端"' not in src, "又写回手写数字了"
    assert "_counts()" in src


def test_chinese_numeral_falls_back_past_ten() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_tour", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    assert mod._cn(5) == "五" and mod._cn(11) == "11"


# ── B272：成片比素材旧时必须能查出来 ────────────────────────────
def test_check_mode_reports_fresh_when_video_is_newer(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    import importlib.util
    import os
    import time

    spec = importlib.util.spec_from_file_location("build_tour", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)

    out = tmp_path / "demo"
    (out / "terminal").mkdir(parents=True)
    video = out / "hos-ares-tour.mp4"
    video.write_bytes(b"x")
    for name in ("manifest.json", "console-tour.mp4"):
        (out / name).write_text("{}", encoding="utf-8")
    (out / "terminal" / "terminal-tour.mp4").write_bytes(b"x")
    # 素材都比成片旧 → 不该报过期
    old = time.time() - 3600
    for p in out.rglob("*"):
        os.utime(p, (old, old))
    os.utime(video, None)
    monkeypatch.setattr(mod, "OUT", out)
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    assert mod.staleness() == []


def test_check_mode_flags_a_newer_material(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    import importlib.util
    import os
    import time

    spec = importlib.util.spec_from_file_location("build_tour", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)

    out = tmp_path / "demo"
    out.mkdir()
    video = out / "hos-ares-tour.mp4"
    video.write_bytes(b"x")
    manifest = out / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    old = time.time() - 3600
    os.utime(video, (old, old))
    monkeypatch.setattr(mod, "OUT", out)
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    assert mod.staleness() == ["manifest.json"] or mod.staleness() == ["demo/manifest.json"]
