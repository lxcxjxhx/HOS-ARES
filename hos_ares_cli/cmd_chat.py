"""B77 · `hos-ares chat`：交互式 agent 界面（`H20 B77`）。

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
import json
import os
import io
import sys
from typing import TYPE_CHECKING, Any, Mapping
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hos_ares_core.agent.client import ModelClientError  # noqa: E402
from hos_ares_core.agent.intent import Plan, Step, parse  # noqa: E402
from hos_ares_core.agent.session import Session  # noqa: E402
from hos_ares_core.store import Store  # noqa: E402
from hos_ares_core.workbench.danger import check_command  # noqa: E402
from hos_ares_core.workbench.executor import make_subprocess_executor  # noqa: E402

if TYPE_CHECKING:
    from hos_ares_core.workbench.sandbox_exec import ShellExecutor
from hos_ares_core.workbench.fs import Workbench  # noqa: E402

from ._io import setup_stdio  # noqa: E402

NL = chr(10)
PROMPT = "> "

#: chat 能调的工具（与 _exec_step 的分支一一对应）
CHAT_TOOLS = frozenset("read write edit patch sh status run platform".split())

#: chat 自己的系统提示（B95）。**不能复用判定链路那句"只选 oracle"**：
#: 那会把模型固定引导到 run_oracle，然后被 CHAT_TOOLS 拒掉、每轮降级 rules。
#: 字段名必须与 ``_exec_step`` 读的键完全一致，否则参数对不上。
#: 已接上的 MCP 工具：`mcp__<server>__<tool>` → (client, tool)（B523）。
#: 用模块级表与 `tools/http.py` 的 `_GUARD` 同一模式：外部依赖进程级，不在每层签名里传。
_MCP: dict[str, tuple[Any, Any]] = {}


def _mcp_tool_names() -> frozenset[str]:
    return frozenset(_MCP)


def _load_mcp(cfg_path: str) -> list[tuple[Any, dict[str, Any]]]:
    """读配置并起 MCP servers。返回 `[(client, {工具名: MCPTool})]`，由调用方负责关闭。

    配置形态（够用就好）：
        {"servers": {"<名字>": {"command": ["python", "server.py"]}}}
    起不来的 server **只警告不中断**——外部能力缺席不该让整个会话开不了。
    """
    if not cfg_path:
        return []
    cfg_file = Path(cfg_path)
    if not cfg_file.exists():
        _say(f"{C_DIM}（MCP 配置不存在：{cfg_file}，跳过）{C_OFF}")
        return []
    try:
        cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _say(f"{C_DIM}（MCP 配置读不了：{exc}，跳过）{C_OFF}")
        return []

    from hos_ares_core.mcp import MCPClient, MCPError

    loaded: list[tuple[Any, dict[str, Any]]] = []
    for name, spec in (cfg.get("servers") or {}).items():
        cmd = (spec or {}).get("command")
        if not isinstance(cmd, list) or not cmd:
            _say(f"{C_DIM}（MCP {name}：command 不是数组，跳过）{C_OFF}")
            continue
        client = MCPClient([str(x) for x in cmd])
        try:
            client.__enter__()
            client.initialize()
            tools = {t.name: t for t in client.list_tools()}
        except MCPError as exc:
            _say(f"{C_DIM}（MCP {name} 起不来：{exc}）{C_OFF}")
            try:
                client.__exit__()
            except Exception:  # noqa: BLE001
                pass
            continue
        for tname, tool in tools.items():
            _MCP[f"mcp__{name}__{tname}"] = (client, tool)
        loaded.append((client, tools))
        _say(f"{C_DIM}MCP {name}：{len(tools)} 个工具{C_OFF}")
    return loaded


CHAT_SYSTEM_PROMPT = (
    "你是 HOS-ARES 的工作台助手：把用户的一句话翻译成工具调用。"
    "回一个 json 对象 {\"tool\": <名字>, \"args\": {...}}；需要读多个文件时"
    "可以回一个 json 数组 [{\"tool\"...}, {\"tool\"...}]，按顺序执行。不要解释。"
    "可用工具与参数（字段名必须完全一致，多一个都会被拒；无参工具给 {}）："
    "read{path} · write{path,content} · edit{path,old,new} · "
    "patch{patches:[{path,old,new},…]} · sh{cmd} · "
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


def _is_chat_tool(name: str) -> bool:
    """内置工具 ∪ 已接上的 MCP 工具（B523）。"""
    return name in CHAT_TOOLS or name in _MCP


class _StreamEcho:
    """流式增量显示（B525）。**写 stderr**：stdout 是会话输出，不能混进模型草稿。"""

    def __init__(self) -> None:
        self.started = False

    def __call__(self, delta: str) -> None:
        if not self.started:
            sys.stderr.write(f"{C_DIM}… 模型输出中：{C_OFF}")
            self.started = True
        sys.stderr.write(delta)
        sys.stderr.flush()

    def done(self) -> None:
        if self.started:
            sys.stderr.write(chr(10))
            sys.stderr.flush()
            self.started = False


def _render_plan(plan: Plan) -> None:
    _say(f"{C_BOLD}⏺ 计划{C_OFF}  {C_DIM}{plan.understood_as}{C_OFF}")
    for i, s in enumerate(plan.steps, start=1):
        _say(_step_line(i, s.tool, dict(s.args), s.why))
        mark = _danger_mark(s)
        if mark:
            _say(f"     {mark}")


def _plan_from_model(client: Any, text: str, history: list[str]) -> tuple[Plan | None, str]:
    """问模型「这句话该调哪个工具」。返回 ``(plan, 降级原因)``。

    给不出合规调用时 plan 为 None，原因非空，由调用方决定**说一次还是每次都说**。
    两类原因措辞必须不同（B228/B234）：模型层失败（401/404/超时）与"给了不合规的调用"
    对客户意味着完全不同的下一步，混成一句"未给出合规调用"会把人引向错误的方向。

    校验走 ``to_tool_call`` 那套窄 schema 的一部分：未知工具 / 多调用 / 缺字段一律拒。
    拒绝不是错误，是"这个模型这轮用不上"，要退回规则层而不是猜。
    """
    ctx = {"state": {"step": len(history), "pending": []}, "tools": {}}
    try:
        raw = client(text, ctx)
    except ModelClientError as exc:
        return None, f"模型层失败：{exc}——本轮降级 rules"
    except Exception:  # noqa: BLE001 —— 模型层失败一律降级，不打断会话
        return None, "模型调用异常——本轮降级 rules"

    # **按 chat 自己的工具集校验**，不套 agent 的 oracle 白名单：
    # `to_tool_call` 的白名单是 SCHEMAS ∪ surface_names()，面向判定链路；
    # chat 要的是 read/write/edit/sh/run/platform。用错表会把合规调用判成非法。
    why = "模型给了不合规的调用——本轮降级 rules"
    if isinstance(raw, (list, tuple)):
        # B521：允许模型一次给多个调用（codex 式并行读取）。
        # 判定链路仍守 `tool_calls_per_step=1`（08 §2），但 chat 不做判定；
        # 执行层本来就是逐步执行，写操作天然串行，不存在并发写竞态。
        steps: list[Step] = []
        for item in raw:
            if not isinstance(item, dict):
                return None, why
            nm = str(item.get("tool") or item.get("name") or "")
            if not nm or nm == "finish" or not _is_chat_tool(nm):
                return None, why
            aa = item.get("args") or item.get("arguments") or {}
            if not isinstance(aa, dict):
                return None, why
            steps.append(Step(nm, aa, "模型选择"))
        if not steps:
            return None, why
        return Plan(raw=text, steps=tuple(steps),
                    understood_as=f"模型：{len(steps)} 个调用", confident=True), ""
    if not isinstance(raw, dict):
        return None, why
    name = str(raw.get("tool") or raw.get("name") or "")
    if not name or name == "finish" or not _is_chat_tool(name):
        return None, why
    args = raw.get("args") or raw.get("arguments") or {}
    if not isinstance(args, dict):
        return None, why
    return Plan(
        raw=text,
        steps=(Step(name, args, "模型选择"),),
        understood_as=f"模型：{name}",
        confident=True,
    ), ""


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


def _exec_step(step: Any, *, wb: Workbench, root: Path, allow_dangerous: bool = False,
               store: Any = None, task_id: str = "") -> tuple[bool, str]:
    tool = step.tool
    a = dict(step.args)

    # 先走工具面（B545）：`read/write/edit/sh` 与工具面的 `fs.*` / `shell.run` 一一对应，
    # 而**审批门与审计只在工具面里**。放在最前面是关键——后面那些 if 分支都是
    # "工具面不认这个工具"时才该落到的实现。
    if tool in ("read", "write", "edit", "sh"):
        # `read` 不需要审批；写与执行需要（与工具面的 impact 分级一致）
        approved = allow_dangerous if tool == "sh" else True
        if tool == "sh":
            hit = check_command(str(a.get("cmd", "")))
            if hit is not None and not allow_dangerous:
                return False, (f"[GATE-DANGER] {hit.name}：{hit.why}"
                               "（确实要跑就加 --allow-dangerous）")
            approved = True          # 危险词已过闸或用户显式放行
        bridged = _surface_call(tool, a, root=root, approved=approved,
                                store=store, task_id=task_id)
        if bridged is not None:
            return bridged

    if tool.startswith("mcp__"):
        entry = _MCP.get(tool)
        if entry is None:
            return False, f"未接上的 MCP 工具：{tool}"
        client, spec = entry
        try:
            is_err, text = client.call_tool(spec.name, a)
        except Exception as exc:  # noqa: BLE001 —— 外部进程的失败要说清
            return False, f"MCP 调用失败：{exc}"
        head = f"{C_DIM}[{tool}]{C_OFF} "
        body = text if len(text) <= 4000 else text[:4000] + "…（截断）"
        return (not is_err), head + body

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
        from hos_ares_cli import cmd_run

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
        from hos_ares_cli import cmd_platform

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

    if tool == "patch":
        # B521：多文件多处替换（codex 的 apply_patch 那个位置）。
        # 逐条执行、任一条失败不影响其余，最后汇总——**不会**因为第三条写错就丢掉前两条的结果。
        items = a.get("patches")
        if not isinstance(items, list) or not items:
            return False, "patch 需要 patches: [{path, old, new}, ...]"
        patch_lines: list[str] = []
        all_ok = True
        for item in items:
            if not isinstance(item, dict):
                patch_lines.append(f"{C_RED}✗ 条目不是对象{C_OFF}")
                all_ok = False
                continue
            path = item.get("path")
            old, new = item.get("old"), item.get("new")
            if not (isinstance(path, str) and isinstance(old, str) and isinstance(new, str)):
                patch_lines.append(f"{C_RED}✗ 条目缺 path/old/new：{item}{C_OFF}")
                all_ok = False
                continue
            fp = root / path
            before = fp.read_text(encoding="utf-8", errors="replace") if fp.exists() else ""
            try:
                edit_res = wb.edit(path, old=old, new=new)
            except Exception as exc:  # noqa: BLE001 —— 单条失败要说清是哪条
                patch_lines.append(f"{C_RED}✗ {path}：{exc}{C_OFF}")
                all_ok = False
                continue
            after = fp.read_text(encoding="utf-8", errors="replace")
            body, added, removed = _diff_text(before, after, path)
            patch_lines.append(f"{C_GREEN}✓{C_OFF} {path}  替换 {edit_res.replacements} 处  "
                       f"{C_GREEN}+{added}{C_OFF} {C_RED}-{removed}{C_OFF}")
            if body:
                patch_lines.append(body)
        return all_ok, chr(10).join(patch_lines)

    if tool == "sh":
        # 用户给的是**完整命令行**（"执行 python -V"），不是脚本正文。
        # 所以交给 shell（Windows: cmd /c；POSIX: sh -c），不要按"以 python 开头"
        # 就当成 python 脚本 —— 那会把 `python -c "print(42)"` 再套一层 python -c。
        interp = "cmd" if sys.platform == "win32" else "sh"
        hit = check_command(a["cmd"])
        if hit is not None and not allow_dangerous:
            return False, (f"[GATE-DANGER] {hit.name}：{hit.why}"
                           "（确实要跑就加 --allow-dangerous）")
        runner, sandbox_note = _chat_executor(root)
        code, out, err = runner(a["cmd"], interp, str(root), 60, {})
        text = (out or err or "").strip()
        head = "\n".join(text.splitlines()[:20])
        mark = "✓" if code == 0 else "✗"
        # 放行也要留痕（B215）：拒绝有记录，override 不能是静默的
        tag = f"[DANGER-OVERRIDE {hit.name}] " if hit is not None else ""
        # 隔离档位必须出现在输出里（B546）：客户看到 exit 码的同时，
        # 也要知道那条命令到底在哪跑过 —— 宿主副作用是真的，容器副作用是零。
        return code == 0, f"{tag}{mark} exit={code}  [{sandbox_note}]\n{head}"

    return False, f"未知工具：{tool}"


def _chat_executor(root: Path) -> "tuple[ShellExecutor, str]":
    """给 `sh` 工具挑一个执行器（B546）。返回 ``(可调用, 说明)``。

    三档，**每档都如实写在输出里**：

    | 档 | 条件 | 落在哪 |
    |---|---|---|
    | 容器 | 有容器档执行体（docker / WSL podman） | 容器里跑，`root` 只读挂进 `/work` |
    | 宿主（显式降级） | 没有容器，但用户没关 | 宿主跑，**输出里标明 `isolation: host`** |
    | 无 | 容器不可用且 `HOS_ARES_REQUIRE_SANDBOX=1` | 每次返回 125 + 原因，不执行 |

    为什么默认降级而不是直接拒绝：`chat` 的 `sh` 是**本地工作台**能力
    （跑 `python -V`、`git status` 这类），把它一刀切掉会让 codex 那半边不可用。
    但**降级必须可见**——所以说明里写 `isolation: host`，报告与终端都看得到。
    要强制隔离就设 `HOS_ARES_REQUIRE_SANDBOX=1`。
    """
    from hos_ares_core.sandbox.runner import select_executor
    from hos_ares_core.workbench.executor import make_subprocess_executor
    from hos_ares_core.workbench.sandbox_exec import (
        WORK_DIR,
        make_sandbox_executor,
        unavailable_executor,
    )

    require = os.environ.get("HOS_ARES_REQUIRE_SANDBOX", "").strip() == "1"
    executor = select_executor()
    if executor is not None and getattr(executor, "isolation", "") == "container":
        image = os.environ.get("HOS_ARES_SANDBOX_IMAGE", "").strip() or "python:3.13-slim"
        return (
            make_sandbox_executor(executor, image=image, mounts=[(str(root), WORK_DIR)]),
            f"isolation: container（{executor.name}·{image}·/work 只读）",
        )
    if require:
        return (
            unavailable_executor("没有容器档执行体，且 HOS_ARES_REQUIRE_SANDBOX=1：拒绝在宿主执行"),
            "isolation: none（要求隔离但不可用，命令不会执行）",
        )
    return (
        make_subprocess_executor(),
        "isolation: host（没有容器运行时——这一步在宿主上跑，副作用是真的）",
    )


def _surface_call(
    tool: str, args: Mapping[str, Any], *, root: Path, approved: bool,
    store: Any = None, task_id: str = "",
) -> tuple[bool, str] | None:
    """先试工具面；**不在工具面上的工具返回 None**（调用方走自己的实现）。

    为什么要有这一层（B545）：B47 声称"工具面统一"，实际 chat 自己另写了一套
    `read/write/edit/sh`，名字与工具面（`fs.read` / `shell.run`…）没有交集。
    后果不只是"两套实现"——**审批门与审计只在工具面里**，走 chat 自己那条路
    等于绕过了它们。

    返回 `(ok, 文本)`：工具面调到了（成功、失败、blocked 都算）；
    `None` 表示"这个名字工具面不认"，调用方按原路走。
    """
    from hos_ares_core.workbench import surface_bridge

    try:
        name = surface_bridge.surface_name(tool)
    except surface_bridge.NotOnSurface:
        return None

    surface = _chat_surface(root, store=store, task_id=task_id)
    payload = surface_bridge.surface_args(tool, args)
    if tool == "sh":
        payload.setdefault("interpreter", "sh" if sys.platform != "win32" else "cmd")
        payload.setdefault("cwd", str(root))
        payload.setdefault("timeout_s", 60)
    before = _surface_diff(tool, args, root=root)
    try:
        result = surface.invoke(name, payload, approved=approved)
    except Exception as exc:  # noqa: BLE001 —— 工具面的异常归一成一句话
        return False, f"✗ {name}：{type(exc).__name__}: {exc}"
    if not result.get("ok"):
        reason = str(result.get("reason") or result.get("outcome") or "失败")
        return False, f"✗ {name}：{reason}"
    text = _format_surface_result(name, result)
    if before or tool in ("write", "edit"):
        after = (root / str(args.get("path", "")))
        new_text = after.read_text(encoding="utf-8", errors="replace") if after.exists() else ""
        body, added, removed = _diff_text(before, new_text, str(args.get("path", "")))
        stat = f"  {C_GREEN}+{added}{C_OFF} {C_RED}-{removed}{C_OFF}"
        text = text + stat + ("\n" + body if body else "")
    return True, text


def _format_surface_result(name: str, result: Mapping[str, Any]) -> str:
    """工具面的返回 → 终端那几行。**只格式化，不加工事实。**"""
    if name == "fs.read":
        text = str(result.get("text") or "")
        lines = text.splitlines()
        head = "\n".join(lines[:12])
        extra = "（已截断）" if result.get("truncated") else ""
        return f"✓ {len(lines)} 行{extra}\n{head}"
    if name == "fs.write":
        return f"✓ 写入 {result.get('written', 0)}B  {result.get('path', '')}"
    if name == "fs.edit":
        return f"✓ {result.get('path', '')}  替换 {result.get('replacements', 0)} 处"
    if name == "shell.run":
        code = result.get("exit_code", 0)
        out = str(result.get("stdout") or result.get("stderr") or "").strip()
        head = "\n".join(out.splitlines()[:20])
        note = str(result.get("isolation") or "")
        mark = "✓" if code == 0 else "✗"
        tail = f"  [{note}]" if note else ""
        return f"{mark} exit={code}{tail}\n{head}"
    return f"✓ {name}"


def _surface_diff(tool: str, args: Mapping[str, Any], *, root: Path) -> str:
    """写/改类工具执行**前**的旧内容，用来算 diff。

    为什么必须在执行前取：`fs.write` 走的是工具面，返回里只有"写了多少字节"，
    而 chat 的终端一直显示 `+N -M` 与具体行 —— 那是**用户读写改动的唯一视觉反馈**。
    接上工具面不许把它弄丢（这条是实测踩出来的：`test_write_shows_diff` 红）。
    """
    if tool not in ("write", "edit"):
        return ""
    path = root / str(args.get("path", ""))
    if not path.exists() or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _chat_surface(root: Path, *, store: Any = None, task_id: str = "") -> Any:
    """chat 用的工具面：工作台 + 容器执行器 + 审计落库（能接的都接上）。

    每次调用重建是**有意的**：`root` 可能不同（`--root`），
    而工具面的 handler 闭包里握着 workbench，缓存错 root 比重建贵得多。

    ``store`` / ``task_id`` 给了就挂上 **audit sink**（B548）：每条审计同时进
    `task_events`，于是"谁在什么时候调了什么、允不允许、结果如何"**跨进程可查**。
    没给就只留在内存里 —— 那种情况下这条信息只在会话当场能看到。
    """
    from hos_ares_core.tools.surface import build_surface
    from hos_ares_core.workbench.fs import Workbench

    runner, note = _chat_executor(root)

    def _shell(args: Mapping[str, Any]) -> Mapping[str, Any]:
        code, out, err = runner(
            str(args.get("script") or ""),
            str(args.get("interpreter") or "sh"),
            str(args.get("cwd") or root),
            int(args.get("timeout_s") or 60),
            {},
        )
        # 隔离档位随返回值一起上去：换了执行路径**不许让这条信息消失**（B545）
        return {"exit_code": code, "stdout": out, "stderr": err, "isolation": note}

    surface = build_surface(workbench=Workbench(str(root)), shell_runner=_shell)
    if store is not None and task_id:
        surface.audit_sink = _audit_to_events(store, task_id)
    return surface


def _audit_to_events(store: Any, task_id: str) -> Any:
    """把工具面审计写进 `task_events`（kind=`tool`）。

    复用现成的表与列（`tool` / `ok` / `detail_json`），不新建表：
    审计事件本来就是"任务做过的一件事"，与 oracle 事件同类。

    **失败不上抛**：兜在 `ToolSurface._record` 里——审计写不进去是运维问题，
    不该让工具调用本身失败。
    """
    import json as _json
    import time as _time

    def _sink(row: Mapping[str, Any]) -> None:
        tool = str(row.get("tool") or "")
        result = str(row.get("result") or "")
        ok = result == "ok"
        detail = _json.dumps(dict(row), ensure_ascii=False, sort_keys=True)
        with store.lock:
            store.conn.execute(
                "INSERT INTO task_events (task_id, ts, kind, tool, ok, detail_json)"
                " VALUES (?,?,?,?,?,?)",
                (task_id, int(_time.time()), "tool", tool, int(ok), detail),
            )
            store.conn.commit()

    return _sink


def _ask(prompt: str) -> str:
    try:
        return input(prompt)
    except EOFError:
        return "quit"


def main(argv: list[str] | None = None) -> int:
    import argparse

    epilog = (
        "接真模型（B226）：设 HOS_ARES_MODEL_BASE_URL + HOS_ARES_MODEL"
        "（需要鉴权再加 HOS_ARES_MODEL_API_KEY）；不设就用规则层，"
        "启动行会如实显示 `model: rules`。\n"
        "模型只允许一次调用一个工具（tool_calls_per_step=1），不给的字段不许编。"
    )
    ap = argparse.ArgumentParser(prog="hos-ares chat", description="交互式 agent 会话",
                                 epilog=epilog, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(ROOT / "out" / "chat.db"), help="会话事件库")
    ap.add_argument("--root", default=str(ROOT), help="工作根（读写不得逃逸）")
    ap.add_argument("--task-id", default="", help="会话 id（--resume 时指定）")
    ap.add_argument("--resume", action="store_true", help="恢复已有会话")
    ap.add_argument("--yes", action="store_true", help="跳过计划确认（脚本/录屏用）")
    ap.add_argument("--allow-dangerous", action="store_true",
                    help="放行危险命令（删根/格式化/远程脚本直跑等，默认拦）")
    ap.add_argument("--mcp-config", default=os.environ.get("HOS_ARES_MCP_CONFIG", ""),
                    help="MCP servers 配置（json 文件），或设 HOS_ARES_MCP_CONFIG")
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
    # MCP：接上外部工具（B523）。起不来的 server 只警告——外部能力缺席不该让会话开不了。
    _loaded_mcp = _load_mcp(args.mcp_config)
    if _loaded_mcp:
        import atexit

        for _client, _tools in _loaded_mcp:
            atexit.register(_client.__exit__)

    # 决策层如实渲染：没模型就说 rules，不假装
    model_name = ""
    try:
        from hos_ares_core.agent.client import from_env

        _echo = _StreamEcho()
        client = from_env(system_prompt=CHAT_SYSTEM_PROMPT, on_delta=_echo)
        model_name = getattr(client, "model", "") if client is not None else ""
    except Exception:  # noqa: BLE001 —— 客户端构造失败不影响规则模式
        client = None

    layer = f"model: {model_name}" if model_name else "model: rules"
    _say(f"{C_BOLD}HOS-ARES chat{C_OFF} · {layer} · 工作根 {root}")
    _say(f"{C_DIM}会话 {sess.task_id} · 已录 {len(sess.turns)} 步"
         f" · /help 看用法 · Ctrl+C 中断当前轮{C_OFF}")
    if not model_name:
        # 规则模式要说怎么升级（B362）：光看 `model: rules` 不知道这是"没配"还是"就是这样"
        _say(f"{C_DIM}（规则模式：设 HOS_ARES_MODEL_BASE_URL + HOS_ARES_MODEL 接真模型，"
             f"要鉴权再加 HOS_ARES_MODEL_API_KEY；/help 有说明）{C_OFF}")
    _say()

    history: list[str] = [t.instruction for t in sess.turns]
    #: 降级原因 → 次数。**同一条只说一次**：每轮刷同一句，人就不看了（B234）。
    degraded: dict[str, int] = {}

    def _note_degrade(why: str) -> None:
        if why in degraded:
            degraded[why] += 1
            return
        degraded[why] = 1
        _say(f"{C_DIM}{why}{C_OFF}")
        if "模型层失败" in why:
            _say(f"{C_DIM}  `hos-ares doctor` 的「模型后端」项可复现；`/status` 看累计次数{C_OFF}")

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
            _say("  「把 a.txt 里的 foo 改成 bar」「执行 python -V」")
            # 斜杠命令自己也要有一条说明（B249）：/help 以前只教"说人话"，
            # 于是 /status、/history 这些只有读到源码的人知道。
            _say("  斜杠命令：/help 本条 · /status 步数与决策层 · /history 本会话步骤 · /quit 退出")
            continue
        if text == "/history":
            _say(sess.render_history())
            continue
        if text == "/status":
            extra = ""
            if degraded:
                extra = f" · 模型降级 {sum(degraded.values())} 次"
            _say(f"  步数 {len(sess.turns)} · 决策层 {layer} · 库 {db}{extra}")
            continue

        if client is not None:
            model_plan, why = _plan_from_model(client, text, history)
            _echo.done()   # B525：流式增量收尾换行，别把下一行挤在同一行
            if model_plan is None:
                plan = parse(text, history=history)
                _note_degrade(why)
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
                             allow_dangerous=args.allow_dangerous,
                             store=store, task_id=sess.task_id)
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
