"""B77 · `veripen chat`：交互式 agent 界面（`H20 B77`）。

像 Codex / Claude Code 那样**说人话**，而不是敲工具名：

```
> 看下 out/demo/x.txt 里写了什么

⏺ 计划
  1. read out/demo/x.txt
  执行？[Y/n] y

⏺ read(out/demo/x.txt)
  ✓ 12 行 · 214B
  ...
```

三条纪律：

1. **计划先于执行**，且可否决（`n` 不执行）—— 不能"先干了再告诉你干了什么"；
2. **决策层如实渲染**：配了模型显示模型名，没配显示 `model: rules`，
   不假装调过模型；
3. **Ctrl+C 只中断当前轮**，回到提示符，会话不丢（事件已在 `task_events`）。
"""

from __future__ import annotations

import contextlib
import difflib
import io
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from veripen_core.agent.intent import Plan, Step, parse  # noqa: E402
from veripen_core.agent.session import Session  # noqa: E402
from veripen_core.store import Store  # noqa: E402
from veripen_core.workbench.danger import check_command  # noqa: E402
from veripen_core.workbench.executor import make_subprocess_executor  # noqa: E402
from veripen_core.workbench.fs import Workbench  # noqa: E402

from ._io import setup_stdio  # noqa: E402

NL = chr(10)
PROMPT = "> "

#: chat 能调的工具（与 _exec_step 的分支一一对应）
CHAT_TOOLS = frozenset("read write edit sh status run platform".split())

#: chat 自己的系统提示（B95）。**不能复用判定链路那句"只选 oracle"**：
#: 那会把模型固定引导到 run_oracle，然后被 CHAT_TOOLS 拒掉、每轮降级 rules。
#: 字段名必须与 ``_exec_step`` 读的键完全一致，否则参数对不上。
CHAT_SYSTEM_PROMPT = (
    "你是 VeriPen 的工作台助手：把用户的一句话翻译成**恰好一次**工具调用。"
    "只回一个 json 对象 {\"tool\": <名字>, \"args\": {...}}，不要解释，不要多调用。"
    "可用工具与参数（字段名必须完全一致，多一个都会被拒；无参工具给 {}）："
    "read{path} · write{path,content} · edit{path,old,new} · sh{cmd} · "
    "run{target,openapi?,discover?} · platform{profile,openapi} · status{}。"
    "选择规则：要执行命令或查看系统信息用 sh（cmd 是完整命令行）；"
    "读文件用 read；整文件重写用 write；局部替换用 edit；"
    "要跑一次渗透用 run：有契约给 openapi；没有契约给 discover=true（两者二选一，"
    "都没有就问，不要编）；"
    "按档批量跑用 platform；问会话状态用 status。"
    "拿不准就调 status。"
)
C_DIM = "\x1b[2m"
C_BOLD = "\x1b[1m"
C_GREEN = "\x1b[32m"
C_RED = "\x1b[31m"
C_CYAN = "\x1b[36m"
C_OFF = "\x1b[0m"


def _say(text: str = "") -> None:
    print(text)


def _step_line(i: int, tool: str, args: dict[str, Any], why: str) -> str:
    a = ", ".join(f"{k}={v}" for k, v in args.items())
    return f"  {i}. {C_CYAN}{tool}{C_OFF}({a})" + (f"  {C_DIM}— {why}{C_OFF}" if why else "")


def _danger_mark(step: Any) -> str:
    """计划里就把危险命令标出来（B215）。

    "计划先于执行"要能**真的用上**：人看到 `⚠ rm-root` 才会说不。
    等到执行时才拦，等于把否决权拖到事后。
    """
    if getattr(step, "tool", "") != "sh":
        return ""
    cmd = str((step.args or {}).get("cmd") or "")
    hit = check_command(cmd)
    return f"{C_RED}⚠ [{hit.name}] {hit.why}{C_OFF}" if hit is not None else ""


def _render_plan(plan: Plan) -> None:
    _say(f"{C_BOLD}⏺ 计划{C_OFF}  {C_DIM}{plan.understood_as}{C_OFF}")
    for i, s in enumerate(plan.steps, start=1):
        _say(_step_line(i, s.tool, dict(s.args), s.why))
        mark = _danger_mark(s)
        if mark:
            _say(f"     {mark}")


def _plan_from_model(client: Any, text: str, history: list[str]) -> Plan | None:
    """问模型「这句话该调哪个工具」。**给不出合规调用就返回 None**（由调用方降级 rules）。

    校验走 ``to_tool_call``（窄 schema）：未知工具 / 多调用 / 缺字段一律拒。
    拒绝不是错误，是"这个模型这轮用不上"，要退回规则层而不是猜。
    """
    ctx = {"state": {"step": len(history), "pending": []}, "tools": {}}
    try:
        raw = client(text, ctx)
    except Exception:  # noqa: BLE001 —— 模型层失败一律降级，不打断会话
        return None

    # **按 chat 自己的工具集校验**，不套 agent 的 oracle 白名单：
    # `to_tool_call` 的白名单是 SCHEMAS ∪ surface_names()，面向判定链路；
    # chat 要的是 read/write/edit/sh/run/platform。用错表会把合规调用判成非法。
    if isinstance(raw, (list, tuple)):
        return None                      # 一步一调用：多调用直接拒
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("tool") or raw.get("name") or "")
    if not name or name == "finish" or name not in CHAT_TOOLS:
        return None
    args = raw.get("args") or raw.get("arguments") or {}
    if not isinstance(args, dict):
        return None
    return Plan(
        raw=text,
        steps=(Step(name, args, "模型选择"),),
        understood_as=f"模型：{name}",
        confident=True,
    )


def _diff_text(before: str, after: str, path: str) -> tuple[str, int, int]:
    """返回 (diff 文本, 新增行数, 删除行数)。"""
    bl = before.splitlines()
    al = after.splitlines()
    diff = list(difflib.unified_diff(bl, al, fromfile=path, tofile=path, lineterm="", n=0))
    added = sum(1 for d in diff if d.startswith("+") and not d.startswith("+++"))
    removed = sum(1 for d in diff if d.startswith("-") and not d.startswith("---"))
    body = "\n".join(d for d in diff if d[:1] in "+-" and not d.startswith(("+++", "---")))
    return body, added, removed


def _truthy(value: Any) -> bool:
    """模型可能给 true，规则层给 "true"；两种都要认，但**不把非空字符串都当真**。"""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


def _exec_step(step: Any, *, wb: Workbench, root: Path, allow_dangerous: bool = False) -> tuple[bool, str]:
    tool = step.tool
    a = dict(step.args)

    if tool == "status":
        return True, "见上方状态行"

    if tool == "run":
        target = a.get("target", "")
        openapi = a.get("openapi", "")
        discover = _truthy(a.get("discover"))
        if not target:
            return False, "需要目标：跑一下 <url> openapi=<契约路径>，或 discover 表示无契约"
        if openapi and discover:
            return False, "openapi 与 discover 二选一：有契约就别猜面"
        if not openapi and not discover:
            return False, "需要契约：句子里加 openapi=<路径>；没有契约就加 discover（不替你猜）"
        from veripen_cli import cmd_run

        out_dir = ROOT / "out" / "chat" / f"run-{int(time.time())}"
        buf = io.StringIO()
        argv = ["--target", target, "--out", str(out_dir)]
        argv += ["--discover"] if discover else ["--openapi", openapi]
        with contextlib.redirect_stdout(buf):
            try:
                rc = cmd_run.main(argv)
            except SystemExit as exc:      # cmd_run 用退出码表达失败
                rc = int(exc.code or 0)
        full = buf.getvalue()
        out_dir.mkdir(parents=True, exist_ok=True)
        log = out_dir / "stdout.log"
        log.write_text(full, encoding="utf-8")
        tail = NL.join(full.splitlines()[-12:])
        msg = NL.join([
            ("✓" if rc == 0 else "✗") + f" exit={rc}",
            tail,
            f"  完整输出：{log}",
        ])
        return rc == 0, msg

    if tool == "platform":
        name = a.get("profile", "")
        prof = ROOT / "profiles" / name / "profile.yaml"
        if not prof.exists():
            avail = sorted(pp.parent.name for pp in (ROOT / "profiles").glob("*/profile.yaml"))
            return False, (f"没有档 {name!r}。可用：{', '.join(avail) or '（无）'}")
        openapi = a.get("openapi", "")
        if not openapi:
            return False, "需要契约：句子里加 openapi=<路径>（档只给资产与策略，不给契约）"
        from veripen_cli import cmd_platform

        out_dir = ROOT / "out" / "chat" / f"pf-{int(time.time())}"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                rc = cmd_platform.main(["--profile", str(prof), "--openapi", openapi,
                                        "--out", str(out_dir), "--db", str(out_dir / "pf.db"),
                                        # 代签要写清楚是谁签的（B174）：这里签字的其实是
                                        # 聊天里点了 y 的那个人，不是「operator」
                                        "--approve", "--approve-by", "chat"])
            except SystemExit as exc:
                rc = int(exc.code or 0)
        full = buf.getvalue()
        out_dir.mkdir(parents=True, exist_ok=True)
        log = out_dir / "stdout.log"
        log.write_text(full, encoding="utf-8")
        tail = NL.join(full.splitlines()[-12:])
        msg = NL.join([
            ("✓" if rc == 0 else "✗") + f" exit={rc}",
            tail,
            f"  完整输出：{log}",
        ])
        return rc == 0, msg

    if tool == "read":
        res = wb.read(a["path"])
        head = "\n".join(res.text.splitlines()[:12])
        return True, f"✓ {len(res.text.splitlines())} 行 · {res.bytes_total}B\n{head}"

    if tool == "write":
        path = a["path"]
        before = ""
        p = (root / path)
        if p.exists():
            before = p.read_text(encoding="utf-8", errors="replace")
        write_res = wb.write(path, a.get("content", ""), overwrite=True)
        body, added, removed = _diff_text(before, a.get("content", ""), path)
        stat = f"✓ 写入 {write_res.bytes_written}B  {C_GREEN}+{added}{C_OFF} {C_RED}-{removed}{C_OFF}"
        return True, stat + ("\n" + body if body else "")

    if tool == "edit":
        path = a["path"]
        p = (root / path)
        before = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
        edit_res = wb.edit(path, old=a["old"], new=a["new"])
        after = p.read_text(encoding="utf-8", errors="replace")
        body, added, removed = _diff_text(before, after, path)
        stat = f"✓ 替换 {edit_res.replacements} 处  {C_GREEN}+{added}{C_OFF} {C_RED}-{removed}{C_OFF}"
        return True, stat + ("\n" + body if body else "")

    if tool == "sh":
        # 用户给的是**完整命令行**（"执行 python -V"），不是脚本正文。
        # 所以交给 shell（Windows: cmd /c；POSIX: sh -c），不要按"以 python 开头"
        # 就当成 python 脚本 —— 那会把 `python -c "print(42)"` 再套一层 python -c。
        interp = "cmd" if sys.platform == "win32" else "sh"
        hit = check_command(a["cmd"])
        if hit is not None and not allow_dangerous:
            return False, (f"[GATE-DANGER] {hit.name}：{hit.why}"
                           "（确实要跑就加 --allow-dangerous）")
        runner = make_subprocess_executor()
        code, out, err = runner(a["cmd"], interp, str(root), 60, {})
        text = (out or err or "").strip()
        head = "\n".join(text.splitlines()[:20])
        mark = "✓" if code == 0 else "✗"
        # 放行也要留痕（B215）：拒绝有记录，override 不能是静默的
        tag = f"[DANGER-OVERRIDE {hit.name}] " if hit is not None else ""
        return code == 0, f"{tag}{mark} exit={code}\n{head}"

    return False, f"未知工具：{tool}"


def _ask(prompt: str) -> str:
    try:
        return input(prompt)
    except EOFError:
        return "quit"


def main(argv: list[str] | None = None) -> int:
    import argparse

    epilog = (
        "接真模型（B226）：设 VERIPEN_MODEL_BASE_URL + VERIPEN_MODEL"
        "（需要鉴权再加 VERIPEN_MODEL_API_KEY）；不设就用规则层，"
        "启动行会如实显示 `model: rules`。\n"
        "模型只允许一次调用一个工具（tool_calls_per_step=1），不给的字段不许编。"
    )
    ap = argparse.ArgumentParser(prog="veripen chat", description="交互式 agent 会话",
                                 epilog=epilog, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(ROOT / "out" / "chat.db"), help="会话事件库")
    ap.add_argument("--root", default=str(ROOT), help="工作根（读写不得逃逸）")
    ap.add_argument("--task-id", default="", help="会话 id（--resume 时指定）")
    ap.add_argument("--resume", action="store_true", help="恢复已有会话")
    ap.add_argument("--yes", action="store_true", help="跳过计划确认（脚本/录屏用）")
    ap.add_argument("--allow-dangerous", action="store_true",
                    help="放行危险命令（删根/格式化/远程脚本直跑等，默认拦）")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    setup_stdio()

    root = Path(args.root).resolve()
    if not root.exists():
        _say(f"工作根不存在：{root}")
        return 2
    db = Path(args.db)
    db.parent.mkdir(parents=True, exist_ok=True)
    store = Store(str(db)).init()
    sess = Session.resume(store, args.task_id) if args.resume and args.task_id else Session(store)
    wb = Workbench(root)

    # 决策层如实渲染：没模型就说 rules，不假装
    model_name = ""
    try:
        from veripen_core.agent.client import from_env

        client = from_env(system_prompt=CHAT_SYSTEM_PROMPT)
        model_name = getattr(client, "model", "") if client is not None else ""
    except Exception:  # noqa: BLE001 —— 客户端构造失败不影响规则模式
        client = None

    layer = f"model: {model_name}" if model_name else "model: rules"
    _say(f"{C_BOLD}HOS-ARES chat{C_OFF} · {layer} · 工作根 {root}")
    _say(f"{C_DIM}会话 {sess.task_id} · 已录 {len(sess.turns)} 步"
         f" · /help 看用法 · Ctrl+C 中断当前轮{C_OFF}")
    _say()

    history: list[str] = [t.instruction for t in sess.turns]
    while True:
        try:
            line = _ask(f"{C_GREEN}{PROMPT}{C_OFF}")
        except KeyboardInterrupt:
            _say("\n（已中断）")
            return 0

        text = line.strip()
        if not text:
            continue
        if text in ("/quit", "/exit", "quit", "exit"):
            break
        if text == "/help":
            _say("  说人话就行：「看下 out/x.txt」「往 out/y.txt 写 hello」")
            _say("  「把 a.txt 里的 foo 改成 bar」「执行 python -V」「状态」；/quit 退出")
            continue
        if text == "/history":
            _say(sess.render_history())
            continue
        if text == "/status":
            _say(f"  步数 {len(sess.turns)} · 决策层 {layer} · 库 {db}")
            continue

        if client is not None:
            model_plan = _plan_from_model(client, text, history)
            if model_plan is None:
                _say(f"{C_DIM}模型未给出合规调用，本轮降级 rules{C_OFF}")
                plan = parse(text, history=history)
            else:
                plan = model_plan
        else:
            plan = parse(text, history=history)
        if not plan.ok:
            _say(f"{C_DIM}{plan.understood_as}{C_OFF}")
            continue

        _render_plan(plan)
        if not args.yes:
            try:
                ans = _ask("  执行？[Y/n] ").strip().lower()
            except KeyboardInterrupt:
                _say("\n（已中断，未执行）")
                continue
            if ans and ans not in ("y", "yes", "是", "执行"):
                _say(f"{C_DIM}已否决，未执行{C_OFF}")
                sess.record(instruction=text, tool="plan", ok=False,
                            summary=f"用户否决：{plan.understood_as}")
                continue

        failed_at = 0
        for idx, step in enumerate(plan.steps, start=1):
            _say(f"{C_BOLD}⏺ {step.tool}{C_OFF}({', '.join(f'{k}={v}' for k, v in step.args.items())})")
            try:
                ok, out = _exec_step(step, wb=wb, root=root,
                             allow_dangerous=args.allow_dangerous)
            except KeyboardInterrupt:
                _say(f"  {C_RED}中断{C_OFF}")
                ok, out = False, "用户中断"
            except Exception as exc:  # noqa: BLE001 —— 工具失败要如实报，不能把界面崩掉
                ok, out = False, f"✗ {type(exc).__name__}: {str(exc)[:160]}"
            for ln in out.splitlines():
                _say("  " + ln)
            sess.record(instruction=text, tool=step.tool, ok=ok, summary=out[:240])
            if not ok:
                failed_at = idx
                break
        if failed_at:
            # 停止要明说停在第几步、还剩几步——用户才知道世界处于什么状态。
            # 最后一步失败也要说（"没有后续"本身就是信息）。
            remaining = len(plan.steps) - failed_at
            tail = f"，后续 {remaining} 步未执行" if remaining else "（最后一步）"
            _say(f"  {C_RED}已停止：第 {failed_at} 步失败{tail}{C_OFF}")
        history.append(text)
        _say()

    _say(f"{C_DIM}会话结束 · {len(sess.turns)} 步已落库 · {db}{C_OFF}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
