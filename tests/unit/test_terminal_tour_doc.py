"""B267 · 录屏文档要跟得上脚本（三幕 → 五幕）。

改前 `docs/delivery/terminal-tour.md` 写的是三幕（run/agent/platform）、51 行、42 帧、15.07s；
真跑一次 `python scripts/demo/record_terminal.py` 得到的是五幕（run/agent/platform/profile/chat）、
96 行、70 帧、24.87s。脚本早就长了两幕，文档一直停在旧版。

本用例只做**静态对齐**（不跑浏览器）：脚本里声明的幕，文档必须都写到；文档不许再写"三幕"。
"""

from __future__ import annotations

import re
from pathlib import Path
from script_module import load_script_module  # noqa: E402
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "demo" / "record_terminal.py"
DOC = ROOT / "docs" / "delivery" / "terminal-tour.md"


def _scene_ids() -> list[str]:
    src = SCRIPT.read_text(encoding="utf-8")
    return re.findall(r'"id": "([a-z]+)", "title":', src)


def test_doc_lists_every_scene_the_script_emits() -> None:
    doc = DOC.read_text(encoding="utf-8")
    missing = [sid for sid in _scene_ids() if f"`{sid}`" not in doc]
    assert not missing, f"文档没写这些幕：{missing}"


def test_doc_does_not_claim_three_scenes() -> None:
    doc = DOC.read_text(encoding="utf-8")
    assert "三幕" not in doc, "脚本已是五幕，文档不许再写三幕"
    assert "五幕" in doc


def test_doc_numbers_match_the_last_real_run() -> None:
    """行数/帧数/时长写错比不写更糟：读者会拿它当验收基线。"""
    doc = DOC.read_text(encoding="utf-8")
    assert "95 行" in doc and "70 帧" in doc, "实测数字没跟上"
    for stale in ("51 行", "42 帧", "96 行"):
        assert stale not in doc, f"还留着旧的实测数字：{stale}"


# ── B268：判据 D 要能自动比对，且录屏必须可重跑 ──────────────────
def _compare() -> Any:
    import importlib.util

    # 用共用函数（B587）。这处同样把 `assert` 写在了 `module_from_spec` 之后。
    mod: Any = load_script_module("record_terminal", SCRIPT)
    return mod._compare_runs


def test_repeat_run_comparison_detects_identical_runs() -> None:
    compare = _compare()
    prev = {"generated_at": "t0", "scenes": [{"id": "run", "lines": 15},
                                             {"id": "agent", "lines": 9}]}
    cur = {"scenes": [{"id": "run", "lines": 15}, {"id": "agent", "lines": 9}]}
    out = compare(prev, cur)
    assert out["checked"] is True and out["same_scenes"] and out["same_lines"], out


def test_repeat_run_comparison_flags_line_drift() -> None:
    """第一幕被拒绝重跑时行数会从 15 掉到 3——这正是本轮真踩到的坑。"""
    compare = _compare()
    prev = {"generated_at": "t0", "scenes": [{"id": "run", "lines": 15}]}
    cur = {"scenes": [{"id": "run", "lines": 3}]}
    out = compare(prev, cur)
    assert out["same_scenes"] is True and out["same_lines"] is False, out


def test_first_run_is_reported_as_a_baseline_not_a_pass() -> None:
    compare = _compare()
    out = compare(None, {"scenes": [{"id": "run", "lines": 15}]})
    assert out["checked"] is False and "基线" in out["reason"], out


def test_recorder_cleans_its_own_outputs_before_running() -> None:
    """`cmd_run` 对已有 capsule 的目录会拒绝重跑：不清目录，第二次录屏必坏。"""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "shutil.rmtree(target" in src
    for name in ("run", "platform", "profile", "agent.db"):
        assert f'"{name}"' in src, name
