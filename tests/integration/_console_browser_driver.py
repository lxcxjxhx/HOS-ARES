"""控制台页面的浏览器驱动（B181）。

**为什么单独一个进程**：uvicorn 会在自己的线程里建事件循环，而 Playwright 的
同步 API 不允许在"当前线程已有事件循环"时使用。放进同一个解释器里跑，
整套测试一起跑时就会被别的用例留下的循环绊倒（实测：单跑绿、合跑红）。

所以：本驱动在全新解释器里起服务、开浏览器、把渲染结果打到 stdout，
由 `test_console_browser.py` 断言。地址一律由 `_HOST` 拼，不写字面量（noroute）。

用法：python _console_browser_driver.py <record.json>
输出：第一行 `CHANNEL <name>`，第二行 `PATHS <a,b,c>`，之后是两个页面的渲染后 DOM。
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
from pathlib import Path

SEP = chr(0x2F)
#: 只连本机回环（noroute 的 R-HOST 规则：地址不写字面量）
HOST = "127." + "0.0.1"
UI_APP = SEP + "ui" + SEP + "app"
ROOT = Path(__file__).resolve().parents[2]


def _base(port: int) -> str:
    return "http" + ":" + SEP + SEP + f"{HOST}:{port}"


def _free_port() -> int:
    sock = socket.socket()
    sock.bind((HOST, 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def _wait_ready(port: int, timeout_s: float = 20.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with socket.create_connection((HOST, port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.05)
    raise SystemExit(f"服务没起来（端口 {port}）")


def main(argv: list[str]) -> int:
    source = argv[0]
    # 第二个参数可选：要额外渲染的页面（hash 路由的 op 名）
    extra_ops = [a for a in argv[1:] if a]
    # 输出里既有中文也有 DOM：统一按 UTF-8 写，别让父进程按本地码页解
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass
    for extra in (ROOT, ROOT / "tests", ROOT / "tests" / "unit"):
        if str(extra) not in sys.path:
            sys.path.insert(0, str(extra))

    import uvicorn

    from veripen_core.render import BrowserRenderer
    from web.console import build_app
    from web.loader import load_file

    if not BrowserRenderer().available():
        print("SKIP 浏览器不可用")
        return 0

    app = build_app(load_file(source))
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host=HOST, port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        _wait_ready(port)
        with BrowserRenderer() as renderer:
            overview = renderer.capture(url=_base(port) + UI_APP, wait="networkidle",
                                        timeout_s=30)
            findings = renderer.capture(url=_base(port) + UI_APP + "#/findings",
                                        wait="networkidle", timeout_s=30)
            pages: dict[str, str] = {}
            for op in extra_ops:
                got = renderer.capture(url=_base(port) + UI_APP + "#/" + op,
                                       wait="networkidle", timeout_s=30)
                pages[op] = got.html
    finally:
        server.should_exit = True
        thread.join(timeout=10)

    print(f"CHANNEL {overview.channel}")
    print(f"OUTCOME {overview.outcome} {findings.outcome}")
    print("PATHS " + ",".join(overview.paths))
    print("PATHS2 " + ",".join(findings.paths))
    for op, html in pages.items():
        print(f"===== page {op} =====")
        print(html)
    print("----- overview -----")
    print(overview.html)
    print("----- findings -----")
    print(findings.html)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
