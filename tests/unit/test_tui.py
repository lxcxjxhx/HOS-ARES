"""B56 TUI（`H04 §3` / `H07` B56）。

判据：三块面板在 · 只读（无 verdict 写入） · 零第三方依赖 · 无 curses 时降级且 doctor 可见 ·
关闭 TUI 不影响任务（纯函数渲染，不持有任务状态）。
"""

from __future__ import annotations

import io
from pathlib import Path

from veripen_cli.tui import STEP_WINDOW, BUDGET_KEYS, TuiState, line_mode, render_panels

ROOT = Path(__file__).resolve().parents[2]


def _state(**kw: object) -> TuiState:
    base: dict[str, object] = dict(
        task_id="t-1", phase="RUNNING", steps=7,
        budget={"steps": 7, "requests": 12, "tokens": 300, "elapsed_s": 9, "tool_calls": 4},
        limits={"steps": 120, "requests": 200, "tokens": 20000, "elapsed_s": 1800, "tool_calls": 40},
        events=[{"kind": "http", "tool": f"t{i}", "ok": i % 2 == 0} for i in range(12)],
        pending_leads=2, closed_loops=5,
    )
    base.update(kw)
    return TuiState(**base)  # type: ignore[arg-type]


# ── A 三块面板 ────────────────────────────────────────────────
def test_three_panels_present() -> None:
    text = render_panels(_state())
    for marker in ("任务区", "动作流", "线索区"):
        assert marker in text, f"缺面板 {marker}"
    assert "只读" in text


def test_budget_rows_cover_all_five() -> None:
    text = render_panels(_state())
    for key in BUDGET_KEYS:
        assert f"{key}=7/120" in text if key == "steps" else key in text


def test_action_window_is_capped_and_counts_total() -> None:
    text = render_panels(_state())
    shown = [line for line in text.splitlines() if line.strip().startswith(("http", "tool"))]
    assert len(shown) <= STEP_WINDOW
    assert "共 12 步" in text


def test_pending_leads_line_flips_when_closed() -> None:
    assert "必须闭环" in render_panels(_state(pending_leads=3))
    closed = render_panels(_state(pending_leads=0))
    assert "必须闭环" not in closed and "已闭环 5" in closed


def test_empty_events_do_not_crash() -> None:
    assert "暂无动作" in render_panels(_state(events=[]))


# ── B 只读 ───────────────────────────────────────────────────
def test_tui_has_no_verdict_write_path() -> None:
    src = (ROOT / "veripen_cli" / "tui.py").read_text(encoding="utf-8")
    assert "verdict" not in src, "TUI 不许出现 verdict（连字段都不该有）"
    assert "def write" not in src and "def delete" not in src


# ── C 零依赖 ─────────────────────────────────────────────────
def test_no_third_party_imports() -> None:
    """只看 import 语句：注释里提到这些库名是"为什么不用"的说明，不算依赖。"""
    src = (ROOT / "veripen_cli" / "tui.py").read_text(encoding="utf-8")
    imports = [ln.strip() for ln in src.splitlines()
               if ln.strip().startswith(("import ", "from "))]
    joined = " ".join(imports)
    for banned in ("textual", "rich", "prompt_toolkit"):
        assert banned not in joined


def test_curses_is_imported_lazily_and_optional() -> None:
    src = (ROOT / "veripen_cli" / "tui.py").read_text(encoding="utf-8")
    assert "import curses" in src
    assert "except Exception" in src, "curses 不可用必须降级而不是崩"


# ── D 降级可见 ───────────────────────────────────────────────
def test_line_mode_prints_and_returns_zero() -> None:
    buf = io.StringIO()
    assert line_mode(_state(), out=buf) == 0
    assert "任务区" in buf.getvalue()


def test_doctor_reports_tui_mode() -> None:
    """doctor 的 deps 一行里必须写清 TUI 是 curses 还是 line-mode（降级可见）。"""
    from veripen.doctor import run_all

    checks = {c.name: c for c in run_all().checks}
    assert "tui: " in checks["deps"].detail
