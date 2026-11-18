"""控制台启动命令（B37：把原先的临时脚本收进 CLI）。

用法：

    python -m hos_ares_cli.cmd_console --port 8099 --hunt out/b30/hunt.json

纪律：装载器只读映射（`web.loader`），服务只读（`ui.*` 写方法 405）。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from typing import Any, Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # 允许 `python -m` 与直接执行两种方式
    sys.path.insert(0, str(ROOT))

from hos_ares_core.audit_source import load_audit  # noqa: E402
from hos_ares_core.store import Store  # noqa: E402
from web.console import build_app  # noqa: E402
from web.loader import load_approvals, load_file, summarize  # noqa: E402


def artifact_note(path: str) -> str:
    """产物文件在不在——**"0 条"与"没跑过"不是一回事**（B243）。

    `out/` 整个目录不进版本库，所以干净 clone 上第一次开控制台必然指到一个不存在的
    文件；旧行为在两种情况下都只印一行 `数据：0 条`，客户没法判断是不是自己还没跑过。
    """
    p = Path(path)
    if p.is_file():
        return ""
    if p.exists():
        # 指到 run/hunt 的**输出目录**是最常见的用法错误（B276）：
        # 那里面的 findings.json 才是产物，光说"是目录"等于没说下一步。
        inside = p / "findings.json"
        if inside.is_file():
            return f"（是目录，不是产物文件——指到其中的 {inside.as_posix()}）"
        return "（是目录，不是产物文件）"
    return "（不存在：先跑一次 `hos-ares run` 或 `hos-ares hunt`，或换 --hunt 指到产物）"


def port_problem(host: str, port: int) -> str:
    """先试着绑一下端口（B281）。

    以前是先把「控制台：http://host:port/ui/app」打印出来，再交给 uvicorn 去绑；
    端口被占用时 stdout 里已经有那条 URL 了，而进程 exit 1——脚本/CI 会以为控制台起来了。
    uvicorn 的原始报错还夹着 Windows 错误码（gbk 下更是乱码）。
    """
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        # **不要**设 SO_REUSEADDR：Windows 上它会改变语义，端口被占用时报的是
        # WSAEACCES(10013)「拒绝访问」而不是 10048「地址已在使用」，
        # 于是提示会误指向"权限问题"（B281 实测踩到）。
        try:
            sock.bind((host, port))
        except OSError as exc:
            # 别把异常类名甩给客户：Windows 上"端口被占"报的是 PermissionError，
            # 看起来像权限问题。按错误码翻译成人话（10048 占用 / 10013 特权端口）。
            winerror = getattr(exc, "winerror", None)
            if winerror == 10048 or exc.errno in (48, 98, 10048):
                why = "已被占用"
            elif winerror == 10013 or exc.errno == 13:
                why = "被系统拒绝（1024 以下端口通常要管理员权限）"
            else:
                why = f"绑不上：{exc.strerror or exc}"
            return f"端口 {port} {why}——换一个 --port，或先停掉占用该端口的进程"
    return ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hos-ares console", description="只读控制台")
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--host", default="127.0.0.1")
    # `--record` 是别名（B309）：desktop 那条路叫 --record，console 叫 --hunt，
    # 而 load_file 两者都吃（文件/目录/platform 目录都行）。名字比行为窄，就统一给别名。
    ap.add_argument("--hunt", "--record", dest="hunt", default="",
                    help="运行产物：hunt.json / findings.json / 目录（与 desktop 的 --record 同义；"
                         "缺省时按 out/hunt.json → out/run/findings.json → out/demo/findings.json 找）")
    ap.add_argument("--db", default="",
                    help="事件库（供 ui.audit 读取；缺省或库不存在时该页 source=unavailable）")
    args = ap.parse_args(argv)

    # 缺省不能写死某个历史路径（B309）：`out/b30/hunt.json` 早就没人产出了，
    # 于是裸跑 `hos-ares console` 永远是空态，哪怕 `out/run/findings.json` 就在那儿。
    from hos_ares_cli.desktop import pick_record

    # 名字不能叫 `record`：下面还有个同名回调（审批落库），会把它盖掉（B309 实测踩到）。
    record_path = args.hunt or str(pick_record("") or "")
    state = load_file(record_path) if record_path else load_file("")

    audit_rows: list[dict[str, object]] = []
    approval_rows: list[Any] = []
    db_note = "未接"
    if args.db:
        db = Path(args.db)
        if db.exists():
            store = Store(str(db))
            try:
                audit_rows = load_audit(store)
                # 审批页也读这个库（B372）：以前只有 audit 页接库，approvals 页永远空着
                approval_rows = load_approvals(store)
            finally:
                store.close()
            db_note = f"{db}（审计 {len(audit_rows)} 条 / 审批 {len(approval_rows)} 条）"
        else:
            db_note = f"{db}（不存在，audit 页 unavailable）"

    if audit_rows:
        state = replace(state, audit=audit_rows)
    if approval_rows:
        state = replace(state, approvals=approval_rows)

    stats = summarize(state)

    # 审批决定落库（B193）：控制台是驭智面的审批入口，决定不能只活在本进程里。
    record = None
    if args.db and Path(args.db).exists():
        from hos_ares_core.blackboard import EventLog

        def record(approval: Mapping[str, Any]) -> str:
            store = Store(str(Path(args.db)))
            try:
                EventLog(store).record(
                    task_id="console",
                    kind="approval",
                    ok=str(approval.get("status")) == "approved",
                    detail={
                        # `by` 是审计页 `who` 列的来源（audit_source.to_row）
                        "by": approval.get("decided_by") or "-",
                        "approval_id": approval.get("id"),
                        "action_class": approval.get("action_class"),
                        "asset": approval.get("asset"),
                        "decision": approval.get("status"),
                        "reason": approval.get("reason"),
                    },
                )
            finally:
                store.close()
            return "task_events(kind=approval)"

    app = build_app(state, record_decision=record)

    # `--port 0` = 向系统要一个空闲端口（B310）：与 desktop 的默认行为对齐，
    # 否则同时开两个控制台就得自己猜哪个端口空着。
    if args.port == 0:
        from hos_ares_cli.desktop import free_port

        args.port = free_port()

    problem = port_problem(args.host, args.port)
    if problem:
        print(f"控制台起不来：{problem}", file=sys.stderr)
        return 2

    print(f"控制台：http://{args.host}:{args.port}" + chr(0x2F) + "ui" + chr(0x2F) + "app")
    print(f"数据：{stats['total']} 条（{stats['verified']} 已确认 / "
          f"{stats['unverified']} 未验证，其中 blocked {stats['blocked']}）")
    print(f"来源：{record_path or '（未找到产物，空态）'}"
          f"{artifact_note(record_path) if record_path else ''}")
    print(f"审计事件：{db_note}")

    import uvicorn

    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    except OSError as exc:
        # 探针到绑定之间仍有极小窗口（B281 的开口）：端口在这中间被抢走时，
        # uvicorn 会抛原始 OSError。上面那行 URL 已经打出去了，所以必须**当场否认它**，
        # 否则日志里留下一条假消息。
        winerror = getattr(exc, "winerror", None)
        why = "已被占用" if winerror == 10048 or exc.errno in (48, 98, 10048) else str(exc)
        print(f"控制台起不来：端口 {args.port} {why}——上面那行 URL 没有生效", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
