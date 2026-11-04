"""B56 TUI：stdlib `curses`，三块面板；无 curses 时降级纯行模式。

为什么不引 `textual` / `rich`：离线包要能装进内网（`A3`），渲染库会把
"零依赖"变成"要 pip"。所以这里的全部能力都来自标准库，缺了它就退化成纯文本。

三块面板（`H04 §3`）：
- **任务区**：任务 id / 阶段 / 步数 / 五预算余量；
- **动作流**：最近 N 步（kind / 目标 / 结果）；
- **线索区**：pending leads 计数 + 强制闭环提示。

边界：TUI **只读**。任何"改判定/改发现"的入口都不在这里，也不许加。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

NL = chr(10)

#: 动作流最多显示几步
STEP_WINDOW = 8

#: 五预算（与 `04 §7` 一致）
BUDGET_KEYS = "steps requests tokens elapsed_s tool_calls".split()


@dataclass(frozen=True)
class TuiState:
    task_id: str
    phase: str = "RUNNING"
    steps: int = 0
    budget: Mapping[str, Any] = field(default_factory=dict)
    limits: Mapping[str, Any] = field(default_factory=dict)
    events: Sequence[Mapping[str, Any]] = ()
    pending_leads: int = 0
    closed_loops: int = 0


def render_panels(state: TuiState) -> str:
    """纯函数：三块面板渲染成文本。**不碰 IO、不碰判定**，所以能直接断言。"""
    budget_bits = []
    for key in BUDGET_KEYS:
        used = state.budget.get(key, 0)
        limit = state.limits.get(key, "-")
        budget_bits.append(f"{key}={used}/{limit}")

    window = list(state.events)[-STEP_WINDOW:]
    action_lines = [
        f"  {e.get('kind', '?'):10s} {str(e.get('tool') or e.get('oracle_id') or '-'):24s} "
        f"{'ok' if e.get('ok') else 'fail'}"
        for e in window
    ] or ["  （暂无动作）"]

    lead_line = (
        f"  pending leads: {state.pending_leads}（必须闭环）"
        if state.pending_leads
        else f"  pending leads: 0 · 已闭环 {state.closed_loops}"
    )

    lines = [
        f"┌ 任务区 ─ {state.task_id} · {state.phase} · {state.steps} 步",
        "  " + "  ".join(budget_bits),
        f"├ 动作流 ─ 最近 {min(len(state.events), STEP_WINDOW)} 步 / 共 {len(state.events)} 步",
        *action_lines,
        "├ 线索区",
        lead_line,
        "└ 只读：TUI 不能改判定，也不能改发现",
    ]
    return NL.join(lines)


def line_mode(state: TuiState, *, out: Any = None) -> int:
    """无 curses 时的降级：把面板打一遍就退出。**功能是子集，不是残废**。"""
    stream = out or sys.stdout
    stream.write(render_panels(state) + NL)
    return 0


def run(state: TuiState) -> int:
    """curses 主循环；不可用时自动降级（并且 doctor 会显示 `tui: line-mode`）。"""
    try:
        import curses  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - 无 curses / Windows 未装 windows-curses
        return line_mode(state)

    def _draw(screen: Any) -> None:
        screen.erase()
        y = 0
        for line in render_panels(state).split(NL):
            screen.addstr(y, 0, line[: screen.getmaxyx()[1] - 1])
            y += 1
        screen.refresh()
        screen.getch()

    try:
        curses.wrapper(_draw)
    except Exception:  # noqa: BLE001 - 终端不支持（非 tty / 重定向）
        return line_mode(state)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="veripen-tui", description="只读任务面板（stdlib curses）")
    ap.add_argument("--task", default="t-demo")
    ap.add_argument("--once", action="store_true", help="只打印一次（纯行模式）")
    ap.add_argument("--steps", type=int, default=0)
    args = ap.parse_args(argv)

    state = TuiState(task_id=args.task, steps=args.steps,
                     events=[{"kind": "oracle", "oracle_id": "idor_cross_account", "ok": True}])
    return line_mode(state) if args.once else run(state)


if __name__ == "__main__":
    raise SystemExit(main())
