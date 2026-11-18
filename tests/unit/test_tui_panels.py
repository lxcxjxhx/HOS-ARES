"""B290 · TUI 三块面板里的步数与预算。

改前实测（`hos-ares tui --task t-demo --once --steps 2`）：

```
┌ 任务区 ─ t-demo · RUNNING · 2 步          ← --steps 的**上限**被当成步数
  steps=0/-  requests=0/- …                 ← 看起来像"真的用了 0"
├ 动作流 ─ 最近 1 步 / 共 1 步              ← 真实步数在这里
```

同一屏三个步数。头部改成真实步数 +（上限 N），预算未知时直说"未接"。
"""

from __future__ import annotations

# ── B290：一屏里不许出现三个步数 ────────────────────────────────
def test_header_counts_real_steps_not_the_limit() -> None:
    """`--steps` 是**上限**；以前直接当步数写进头部，于是同一屏出现 2 / 1 / 0 三个数。"""
    from hos_ares_cli.tui import TuiState, render_panels

    state = TuiState(task_id="t", steps=2, events=[{"kind": "oracle", "ok": True}])
    text = render_panels(state)
    assert "· 1 步（上限 2）" in text, text
    assert "· 2 步" not in text, "又用上限冒充步数了"


def test_unknown_budget_is_not_printed_as_zeros() -> None:
    """`steps=0/- requests=0/- …` 看起来像"真的用了 0"，而动作流说"共 1 步"。"""
    from hos_ares_cli.tui import TuiState, render_panels

    text = render_panels(TuiState(task_id="t", events=[{"kind": "oracle", "ok": True}]))
    assert "预算：未接" in text, text
    assert "steps=0/-" not in text, text


def test_known_budget_still_renders_numbers() -> None:
    from hos_ares_cli.tui import TuiState, render_panels

    state = TuiState(task_id="t", budget={"steps": 3}, limits={"steps": 20},
                     events=[{"kind": "oracle", "ok": True}])
    text = render_panels(state)
    assert "steps=3/20" in text, text
