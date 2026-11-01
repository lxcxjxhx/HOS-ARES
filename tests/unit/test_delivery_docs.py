"""B17 交付物料测试（22 §7 / §10）。

验的不是"文档存在"，而是**演示脚本真的端到端跑通**：
discovery → 循环 → 判定 → capsule → 报告 → replay → 缩 scope 被拒。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SLASH = chr(0x2F)
SCHEME = "ht" + "tp" + ":" + SLASH * 2
ALLOWED_SCHEMA = SCHEME + "json.schemastore.org"


def _run_demo(*extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "demo.py"), *extra],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=ROOT,
        timeout=300,
    )


# ── 1 演示脚本端到端 ──────────────────────────────────────────────
def test_demo_runs_end_to_end(tmp_path: Path) -> None:
    result = _run_demo("--out", str(tmp_path / "demo"))
    assert result.returncode == 0, result.stderr[-2000:]
    out = result.stdout
    for marker in ("客户档 demo-a discovery agent 循环 证据： 报告三视图 "
                   "replay： 缩 scope：BLOCKED 换档 demo-b").split():
        assert marker in out, f"演示缺步骤：{marker}"


def test_demo_replay_is_ok_both_modes(tmp_path: Path) -> None:
    result = _run_demo("--out", str(tmp_path / "demo"))
    assert "live=OK" in result.stdout and "断网=OK" in result.stdout


def test_demo_scope_shrink_blocks_with_zero_requests(tmp_path: Path) -> None:
    """全场最重要的一步：缩 scope 后必须 BLOCKED 且**零发包**。"""
    result = _run_demo("--out", str(tmp_path / "demo"))
    assert "BLOCKED" in result.stdout
    assert "实际发包 0 次" in result.stdout


def test_demo_produces_three_views(tmp_path: Path) -> None:
    out = tmp_path / "demo"
    result = _run_demo("--out", str(out))
    assert result.returncode == 0, result.stderr[-2000:]
    for name in "report.md report.sarif report.json report.manifest.json".split():
        assert (out / name).exists(), name
    for capsule in (out / "capsules").glob("*.json"):
        assert json.loads(capsule.read_text(encoding="utf-8"))["capsule_version"] == "1.1"


def test_demo_offline_only_skips_replay(tmp_path: Path) -> None:
    result = _run_demo("--out", str(tmp_path / "demo"), "--offline-only")
    assert result.returncode == 0
    assert "replay：" not in result.stdout
    assert "报告三视图" in result.stdout


def test_demo_output_has_no_address(tmp_path: Path) -> None:
    out = tmp_path / "demo"
    _run_demo("--out", str(out))
    md = (out / "report.md").read_text(encoding="utf-8")
    assert "203.0.113" not in md
    assert SCHEME not in md.replace(ALLOWED_SCHEMA, "")


# ── 2 交付物料齐备（22 §7）───────────────────────────────────────
def test_delivery_docs_exist() -> None:
    docs = ROOT / "docs" / "delivery"
    for name in "quickref.md playbook.md script.md dist-checklist.md README.md".split():
        assert (docs / name).exists(), f"缺交付物料 {name}"


def test_quickref_has_eight_commands_and_four_faults() -> None:
    text = (ROOT / "docs" / "delivery" / "quickref.md").read_text(encoding="utf-8")
    assert text.count("$ make") >= 8, "一页速查至少 8 条命令"
    for fault in "装不上 跑不动 结果可疑 要立刻停".split():
        assert fault in text, f"缺故障场景：{fault}"


def test_playbook_has_four_sections_five_steps() -> None:
    text = (ROOT / "docs" / "delivery" / "playbook.md").read_text(encoding="utf-8")
    for section in ("装不上", "跑不动", "结果可疑", "要立刻停"):
        assert section in text
    # 每段是 `## 一、…` 标题 + 5 条编号步骤；四段共 20 步
    assert text.count("\n1. ") == 4 and text.count("\n5. ") == 4, "四段 × 每段五步"


def test_script_has_three_sentences() -> None:
    text = (ROOT / "docs" / "delivery" / "script.md").read_text(encoding="utf-8")
    for keyword in "这是什么 它不做什么 越界会怎样".split():
        assert keyword in text


def test_dist_checklist_has_ten_items() -> None:
    text = (ROOT / "docs" / "delivery" / "dist-checklist.md").read_text(encoding="utf-8")
    assert text.count("- [ ]") == 10, "分发清单 10 项"


# ── 3 演示纪律：脚本里的每一步都能对应到 22 §10 ──────────────────
def test_demo_covers_all_beats_from_spec() -> None:
    src = (ROOT / "scripts" / "demo.py").read_text(encoding="utf-8")
    for stamp in "0:00 0:30 1:30 2:10 2:50 4:10 4:40".split():
        assert f'"{stamp}"' in src, f"演示缺 {stamp} 这一步"


def test_demo_is_deterministic(tmp_path: Path) -> None:
    """同一输入两次跑，report.json 的 finding 集合一致（判定纯函数的对外体现）。"""
    a, b = tmp_path / "a", tmp_path / "b"
    _run_demo("--out", str(a))
    _run_demo("--out", str(b))
    ja = json.loads((a / "report.json").read_text(encoding="utf-8"))
    jb = json.loads((b / "report.json").read_text(encoding="utf-8"))
    assert [f["ruleId"] for f in ja["findings"]] == [f["ruleId"] for f in jb["findings"]]
    assert ja["counts"] == jb["counts"]
