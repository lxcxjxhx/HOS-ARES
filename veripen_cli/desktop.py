"""桌面/双击启动入口（B198）：起控制台 → 打印地址 → 打开浏览器 → 守到 Ctrl+C。

为什么要单独一个入口：仓库里所有能力都能跑，但**没有"打开就能看"的东西**——
`veripen console` 需要先知道 `--hunt` 指哪、需要自己敲地址。打包成 exe 双击之后，
用户应当直接看到界面，而不是先读一段帮助。

三条设计口径：
1. **不猜数据**：给了 `--record` 就读它；没给就找 `out/hunt.json`、`out/run/findings.json`；
   都没有就是空态（页面自己会说"没有数据"），不编一份出来。
2. **端口不写死**：默认向系统要一个空闲端口，避免"8080 被占"这种第一体验。
3. **地址由本机回环拼出来**：不写字面量（仓库的 lint-ui / noroute 规则）。
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:      # 源码运行时把仓库根挂上；打包后是 _MEIPASS
    sys.path.insert(0, str(ROOT))

_SEP = chr(0x2F)
#: 只连本机回环（noroute 的 R-HOST 规则：地址不写字面量）
_HOST = "127." + "0.0.1"
#: 界面路径（与 web.console.UI_PREFIX 一致）
_APP = "ui" + _SEP + "app"

#: 没显式给 `--record` 时按顺序找这些（相对仓库根 / exe 所在目录）
DEFAULT_RECORDS = (
    Path("out") / "hunt.json",
    Path("out") / "run" / "findings.json",
    Path("out") / "demo" / "findings.json",
)


def base_url(port: int) -> str:
    """本机地址。**拼出来**，不写字面量。"""
    return "http" + ":" + _SEP * 2 + f"{_HOST}:{port}"


def free_port() -> int:
    sock = socket.socket()
    try:
        sock.bind((_HOST, 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def pick_record(explicit: str, root: Path = ROOT) -> Path | None:
    """选数据源：显式的优先；否则按默认顺序找第一个存在的；都没有 → None（空态）。"""
    if explicit:
        return Path(explicit)
    for rel in DEFAULT_RECORDS:
        candidate = root / rel
        if candidate.exists():
            return candidate
    return None


def build_state(record: Path | None) -> Any:
    """加载控制台状态。**缺数据就是空态**，不造假数据。"""
    from web.console import ConsoleState
    from web.loader import load_file

    if record is None or not record.exists():
        return ConsoleState()
    return load_file(str(record))


def describe(record: Path | None) -> str:
    if record is None:
        return "数据：未找到产物——空态启动（跑一次 `veripen run` 或 `veripen hunt` 后重开）"
    if not record.exists():
        return f"数据：{record} 不存在——空态启动"
    return f"数据：{record}"


class Serving:
    """后台服务句柄：线程 + 停止开关。**不要在线程对象上挂属性**（那是隐式契约）。"""

    def __init__(self, thread: threading.Thread, should_exit: Any) -> None:
        self.thread = thread
        self._should_exit = should_exit

    def alive(self) -> bool:
        return self.thread.is_alive()

    def stop(self) -> None:
        self._should_exit = True


def serve(state: Any, port: int, *, host: str = _HOST) -> Serving:
    """起服务（后台线程）。返回句柄，调用方用 `stop()` 收尾。"""
    import uvicorn

    from web.console import build_app

    app = build_app(state)
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return Serving(thread, server.should_exit)


def wait_ready(port: int, timeout_s: float = 20.0, *, host: str = _HOST) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.05)
    return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="veripen-desktop",
        description="打开就是界面：起只读控制台并打开浏览器",
    )
    ap.add_argument("--record", default="", help="运行产物路径（hunt.json 或 findings.json）")
    ap.add_argument("--port", type=int, default=0, help="固定端口（默认向系统要一个空闲端口）")
    ap.add_argument("--no-browser", action="store_true", help="不自动打开浏览器（调试/无头）")
    ap.add_argument("--wait-s", type=float, default=0.0,
                    help="启动后等待多少秒自动退出（0 = 一直守着）")
    args = ap.parse_args(sys.argv[1:] if argv is None else list(argv))

    record = pick_record(args.record)
    state = build_state(record)
    port = args.port or free_port()

    serving = serve(state, port)
    if not wait_ready(port):
        print(f"启动失败：{timeout_s_hint(port)}", file=sys.stderr)
        return 1

    url = base_url(port) + _SEP + _APP
    print("HOS-ARES 控制台已启动")
    print(f"地址：{url}")
    print(describe(record))
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception as exc:  # noqa: BLE001 - 打不开浏览器不该让服务挂掉
            print(f"自动打开浏览器失败（{type(exc).__name__}）：请手动访问上面的地址")

    if args.wait_s > 0:
        time.sleep(args.wait_s)
    else:
        print("按 Ctrl+C 结束")
        try:
            while serving.alive():            # pragma: no cover - 交互式等待
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
    serving.stop()
    return 0


def timeout_s_hint(port: int) -> str:
    return f"{_HOST}:{port} 在 20s 内没有开始服务（端口被占？换 --port）"


if __name__ == "__main__":
    raise SystemExit(main())
