"""B67 · `veripen agent`：交互会话（`H13 B67`）。

坐下干活的地方：一条指令一次动作，每步落 `task_events`，中断不丢、可恢复。

指令白名单（不做"猜你想干什么"）：

| 指令 | 动作 | 落库 tool |
|---|---|---|
| ``read <路径>`` | 读工作根内的文件 | ``read`` |
| ``write <路径> <内容>`` | 新建/覆盖（覆盖需显式，见下） | ``write`` |
| ``edit <路径> <旧> => <新>`` | 精确替换一处 | ``edit`` |
| ``sh <命令>`` | 跑一条命令（五道闸生效） | ``sh`` |
| ``history`` | 打印本次会话动作 | — |
| ``quit`` / EOF | 退出（已完成的步骤都在库里） | — |

**没有 delete，没有"改判定"**——`Workbench` 不提供的方法，这里也不提供。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from veripen_core.agent.session import COMMANDS, Session  # noqa: E402
from veripen_core.store import Store  # noqa: E402
from veripen_core.workbench.executor import make_subprocess_executor  # noqa: E402
from veripen_core.workbench.fs import Workbench  # noqa: E402

from ._io import setup_stdout  # noqa: E402

PROMPT = "agent> "


def _split_instruction(line: str) -> tuple[str, str]:
    line = line.strip()
    if not line:
        return "", ""
    head, _, rest = line.partition(" ")
    return head.lower(), rest.strip()


def _do_read(wb: Workbench, arg: str) -> tuple[bool, str]:
    res = wb.read(arg)
    head = res.text[:200].replace("\n", " ⏎ ")
    return True, f"{res.path} {res.bytes_total}B" + ("（截断）" if res.truncated else "") + f" :: {head}"


def _do_write(wb: Workbench, arg: str) -> tuple[bool, str]:
    path, _, content = arg.partition(" ")
    if not path:
        return False, "用法：write <路径> <内容>"
    res = wb.write(path, content, overwrite=True)
    return True, f"{res.path} 写入 {res.bytes_written}B"


def _do_edit(wb: Workbench, arg: str) -> tuple[bool, str]:
    path, _, body = arg.partition(" ")
    old, _, new = body.partition("=>")
    if not path or "=>" not in body:
        return False, "用法：edit <路径> <旧> => <新>"
    res = wb.edit(path, old=old.strip(), new=new.strip())
    return True, f"{res.path} 替换 {res.replacements} 处"


def _do_sh(root: Path, arg: str) -> tuple[bool, str]:
    if not arg:
        return False, "用法：sh <命令>"
    interp = "python" if "python" in arg.split(" ")[0] else (
        "cmd" if sys.platform == "win32" else "sh"
    )
    runner = make_subprocess_executor()
    code, out, err = runner(arg, interp, str(root), 30, {})
    text = (out or err or "").strip().replace("\n", " ⏎ ")[:200]
    return code == 0, f"exit={code} :: {text}"


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="veripen agent", description="交互会话（工作台）")
    ap.add_argument("--db", default=str(ROOT / "out" / "agent.db"), help="会话事件库")
    ap.add_argument("--root", default=str(ROOT), help="工作根（读写不得逃逸）")
    ap.add_argument("--task-id", default="", help="会话 id（--resume 时指定）")
    ap.add_argument("--resume", action="store_true", help="恢复已有会话")
    ap.add_argument("--max-turns", type=int, default=0, help="0 = 不限")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    setup_stdout()

    root = Path(args.root).resolve()
    if not root.exists():
        print(f"工作根不存在：{root}", file=sys.stderr)
        return 2
    db = Path(args.db)
    db.parent.mkdir(parents=True, exist_ok=True)

    store = Store(str(db)).init()
    sess = Session.resume(store, args.task_id) if args.resume and args.task_id else Session(store)
    wb = Workbench(root)

    print(f"会话 {sess.task_id} · 工作根 {root}")
    print("指令：" + " / ".join(COMMANDS))
    print("模型：未接入（scripted 指令模式）—— 没有假装调过模型")
    if sess.turns:
        print(f"已恢复 {len(sess.turns)} 步：")
        print(sess.render_history())
    print("-" * 62)

    turns_done = 0
    try:
        for raw in sys.stdin:
            if args.max_turns and turns_done >= args.max_turns:
                break
            cmd, arg = _split_instruction(raw)
            if not cmd:
                continue
            if cmd in ("quit", "exit"):
                break
            if cmd == "history":
                print(sess.render_history())
                continue
            if cmd not in COMMANDS:
                print(f"{PROMPT}未知指令：{cmd}（可用：{' '.join(COMMANDS)}）")
                continue

            try:
                if cmd == "read":
                    ok, summary = _do_read(wb, arg)
                elif cmd == "write":
                    ok, summary = _do_write(wb, arg)
                elif cmd == "edit":
                    ok, summary = _do_edit(wb, arg)
                elif cmd == "sh":
                    ok, summary = _do_sh(root, arg)
                else:
                    ok, summary = False, "未实现"
            except Exception as exc:  # noqa: BLE001 —— 指令失败要如实报，不能吞
                ok, summary = False, f"{type(exc).__name__}: {str(exc)[:120]}"

            turn = sess.record(instruction=raw.strip(), tool=cmd, ok=ok, summary=summary)
            mark = "ok " if turn.ok else "ERR"
            print(f"{turn.index:>2} {mark} {cmd:<6} {turn.summary}")
            turns_done += 1
    except KeyboardInterrupt:
        print("\n（已中断；完成的步骤都在库里，用 --resume 继续）")
        return 0

    print("-" * 62)
    print(f"本次 {turns_done} 步 · 会话共 {len(sess.turns)} 步 · 库 {db}")
    if sess.context_facts(1):
        print("最近一步：" + sess.context_facts(1)[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
