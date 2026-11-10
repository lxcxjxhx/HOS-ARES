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
from veripen_core.workbench.danger import check_command  # noqa: E402
from veripen_core.workbench.executor import make_subprocess_executor  # noqa: E402
from veripen_core.workbench.fs import Workbench  # noqa: E402

from ._io import setup_stdio  # noqa: E402

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
    # 新建与覆盖**记成两件事**（B170）：覆盖不可逆，账本里必须看得出来
    if res.created:
        return True, f"{res.path} 新建 {res.bytes_written}B"
    return True, f"{res.path} 覆盖 {res.bytes_written}B（原 {res.bytes_before}B）"


def _do_edit(wb: Workbench, arg: str) -> tuple[bool, str]:
    path, _, body = arg.partition(" ")
    old, _, new = body.partition("=>")
    if not path or "=>" not in body:
        return False, "用法：edit <路径> <旧> => <新>"
    res = wb.edit(path, old=old.strip(), new=new.strip())
    return True, f"{res.path} 替换 {res.replacements} 处"


def _do_glob(wb: Workbench, arg: str) -> tuple[bool, str]:
    """找文件（B171）：工作台早就有 `glob`，REPL 却没暴露——
    于是想定位文件只能 `sh dir /s`，那正是工作台该替人做的事。"""
    pattern = arg.strip() or "**/*"
    paths = wb.glob(pattern)
    if not paths:
        return True, f"0 个文件（pattern={pattern}）"
    head = "、".join(paths[:5]) + ("…" if len(paths) > 5 else "")
    return True, f"{len(paths)} 个文件 :: {head}"


def _do_grep(wb: Workbench, arg: str) -> tuple[bool, str]:
    """搜内容：返回**行号与原文**，不返回文件全量。"""
    pattern = arg.strip()
    if not pattern:
        return False, "用法：grep <正则>"
    matches = wb.grep(pattern)
    if not matches:
        return True, f"0 处匹配（pattern={pattern}）"
    first = matches[0]
    # BOM 要去掉：Windows 上带 BOM 的文件会把 \ufeff 送到摘要里（实测过）
    text = first.text.strip().lstrip("\ufeff")
    return True, f"{len(matches)} 处 :: {first.path}:{first.line} {text[:60]}"


def _do_sh(root: Path, arg: str, *, allow_dangerous: bool = False) -> tuple[bool, str]:
    if not arg:
        return False, "用法：sh <命令>"
    # `sh` 就是"跑一条命令"：解释器由系统 shell 决定，**不按首词猜**（B169）。
    # 早先首词含 python 就把整行塞进 `python -c`，于是 `sh python -V` 变成
    # `python -c "python -V"`，必然 SyntaxError——工具看着能跑，其实跑不了。
    interp = "cmd" if sys.platform == "win32" else "sh"
    hit = None if allow_dangerous else check_command(arg)
    if hit is not None:
        return False, (f"[GATE-DANGER] {hit.name}：{hit.why}"
                       "（确实要跑就加 --allow-dangerous）")
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
    ap.add_argument("--allow-dangerous", action="store_true",
                    help="放行危险命令（删根/格式化/远程脚本直跑等，默认拦）")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    setup_stdio()

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
                elif cmd == "glob":
                    ok, summary = _do_glob(wb, arg)
                elif cmd == "grep":
                    ok, summary = _do_grep(wb, arg)
                elif cmd == "sh":
                    ok, summary = _do_sh(root, arg, allow_dangerous=args.allow_dangerous)
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
