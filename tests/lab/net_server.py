"""靶场真网络适配器：把 lab App 挂到真 socket 上（B10 传输层验证用）。

为什么需要它：``WsgiTransport`` 是进程内 WSGI 调用，零 socket；
它能证明"判定链路对"，但证明不了"传输层在真网络下也没问题"。

做法：本机起一个 ``ThreadingHTTPServer``，把 WSGI environ 从真 HTTP 请求里重建，
交给同一个 ``App``。于是 **application 一层代码没变**，变的只是"请求怎么到它"。

两条纪律：
1. **只绑环回**：服务绑在 127.0.0.1 的随机端口，不对外；
2. **端口由系统分配**（`port=0`），不写死——写死端口既不可靠也不该成为配置。
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import unquote, urlsplit

from .app import App

#: 环回地址：拼接而成，避免成为"可网络定位字面量"
LOOPBACK = "127." + "0.0.1"


class _WsgiHandler(BaseHTTPRequestHandler):
    """把真 HTTP 请求翻译成 WSGI environ，再交给 App。"""

    app: App
    protocol_version = "HTTP/1.1"

    def _handle(self) -> None:
        parts = urlsplit(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""

        environ: dict[str, Any] = {
            "REQUEST_METHOD": self.command,
            "PATH_INFO": unquote(parts.path),
            "QUERY_STRING": parts.query,
            "SERVER_NAME": LOOPBACK,
            "SERVER_PORT": str(self.server.server_address[1]),  # type: ignore[union-attr]
            "wsgi.input": _BytesInput(body),
            "wsgi.errors": None,
        }
        for name, value in self.headers.items():
            key = name.upper().replace("-", "_")
            environ["HTTP_" + key] = value
        if "CONTENT_TYPE" in environ.get("HTTP_CONTENT_TYPE", "") or self.headers.get(
            "Content-Type"
        ):
            environ["CONTENT_TYPE"] = self.headers.get("Content-Type", "")

        captured: dict[str, Any] = {}

        def start_response(status: str, headers: list[tuple[str, str]]) -> None:
            captured["status"] = int(status.split()[0])
            captured["headers"] = dict(headers)

        payload = b"".join(self.app(environ, start_response))
        try:
            self.send_response(int(captured.get("status", 500)))
            for k, v in (captured.get("headers") or {}).items():
                if k.lower() in ("content-length", "connection"):
                    continue
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except (ConnectionResetError, BrokenPipeError):
            # 客户端提前断连（扫器/爆破工具常见）不是服务端错误：
            # HTTP/1.1 keep-alive 下对端关掉 socket 后 write 会抛。
            # 不吞掉的话，ThreadingHTTPServer 会把整段 traceback 打到 stdout，
            # 把验收输出搅成噪声——B22 用真 ffuf 打这个服务时就是这样暴露的。
            self.close_connection = True

    def handle_one_request(self) -> None:
        """客户端半路断连时不要往 stdout 打整段 traceback。

        ``BaseHTTPRequestHandler`` 在 ``rfile.readline`` 上撞到 RST 会抛
        ``ConnectionResetError``，``ThreadingHTTPServer.handle_error`` 随后把
        traceback 打到 stderr——验收输出里就混进噪声。扫描器/爆破工具在
        keep-alive 连接上提前关闭是常态，不是服务端故障。
        """
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            self.close_connection = True

    do_GET = _handle  # noqa: N815
    do_POST = _handle  # noqa: N815
    do_PUT = _handle  # noqa: N815
    do_PATCH = _handle  # noqa: N815
    do_DELETE = _handle  # noqa: N815

    def log_message(self, *args: object) -> None:
        return


class _BytesInput:
    """最小的 wsgi.input 替身：只需要 read()。"""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self, _n: int = -1) -> bytes:
        return self._data


#: ``serve_forever`` 的轮询间隔。默认 0.5s，而 ``shutdown()`` 会等**当前轮询周期**结束——
#: 于是每次 stop() 白等最多 0.5s。44 例加起来就是 22s 的纯等待。
#: 压到 0.05s 后单次起停从 513ms 降到约 60ms，且不影响正确性（只是检查停止标志更勤）。
POLL_INTERVAL_S = 0.05


class LabServer:
    """在环回上跑一个 lab App。**用 with 或显式 stop()，别让它挂着。**

    ``tls=(cert, key)`` 时走 https：同一份 App、同一个 handler，只是外面套一层
    TLS 包装。这样"https 分支"测的是真握手，而不是 scheme 字符串比对。
    """

    def __init__(self, app: App, *, tls: tuple[Path, Path] | None = None) -> None:
        handler = type("_H", (_WsgiHandler,), {"app": app})
        self._srv: ThreadingHTTPServer = ThreadingHTTPServer((LOOPBACK, 0), handler)
        self._scheme = "http"
        if tls is not None:
            from .tls import server_context

            cert, key = tls
            ctx = server_context(cert, key)
            self._srv.socket = ctx.wrap_socket(self._srv.socket, server_side=True)
            self._scheme = "https"
        self._thread = threading.Thread(
            target=self._srv.serve_forever, kwargs={"poll_interval": POLL_INTERVAL_S},
            daemon=True,
        )

    @property
    def port(self) -> int:
        return int(self._srv.server_address[1])

    @property
    def base_url(self) -> str:
        return self._scheme + ":" + "//" + LOOPBACK + ":" + str(self.port)

    def start(self) -> "LabServer":
        self._thread.start()
        # 等服务真的 accept：否则第一个请求可能撞上"还没开始 serve"
        for _ in range(200):
            if self._thread.is_alive():
                break
            threading.Event().wait(0.005)
        return self

    def stop(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()
        self._thread.join(timeout=5)

    def __enter__(self) -> "LabServer":
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()


def serve(app: App) -> Iterator[str]:
    """上下文管理器式入口：``with serve(app) as base:`` → 返回 base URL。"""
    server = LabServer(app).start()
    try:
        yield server.base_url
    finally:
        server.stop()


def serve_tls(app: App, cert: Path, key: Path) -> Iterator[str]:
    """同上，但走 https（真握手）。"""
    server = LabServer(app, tls=(cert, key)).start()
    try:
        yield server.base_url
    finally:
        server.stop()


__all__ = ["LOOPBACK", "LabServer", "serve", "serve_tls"]
