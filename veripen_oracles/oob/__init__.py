"""OOB 通道（B5）：**接入点是符号，不是常量**（07 §3 / 27 N3）。

两种实现：
- ``LabOob``：进程内靶场的回调登记（``tests/lab/app.App.callbacks``），演示与单测用（零网络）。
- ``HttpListener``：本地回环监听，收真实回调；地址由部署注入，代码里没有字面量。

``OobProbe`` 做"前后快照差"：只有**本轮新产生**的、且带预期 nonce 的回调才算命中。
"""

from __future__ import annotations

import hashlib
import http.server
import socket
import threading
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Callback:
    path: str
    source_host: str
    user_agent: str
    at: int

    @property
    def path_sha256(self) -> str:
        return hashlib.sha256(self.path.encode()).hexdigest()[:16]


class OobSink(Protocol):
    def snapshot(self) -> int:
        """返回当前回调计数（单调递增），用于做差。"""

    def since(self, mark: int) -> list[Callback]: ...

    def endpoint(self) -> str:
        """注入给目标的值（符号解析结果，不是常量）。"""


@dataclass
class LabOob:
    """进程内靶场的回调登记。"""

    app: Any
    endpoint_value: str
    _seen: int = field(default=0, init=False)

    def snapshot(self) -> int:
        return len(self.app.callbacks)

    def since(self, mark: int) -> list[Callback]:
        out: list[Callback] = []
        for item in self.app.callbacks[mark:]:
            out.append(
                Callback(
                    path=item.get("url", ""),
                    source_host="lab-app",
                    user_agent=item.get("ua", ""),
                    at=0,
                )
            )
        return out

    def endpoint(self) -> str:
        return self.endpoint_value


class _Handler(http.server.BaseHTTPRequestHandler):
    sink: "HttpListener"

    def do_GET(self) -> None:  # noqa: N802 - http.server 约定
        self.sink._record(self.path, self.client_address[0], self.headers.get("User-Agent", ""))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    do_POST = do_GET  # noqa: N815 - 同一处理

    def log_message(self, *args: object) -> None:  # 静音
        return


class HttpListener:
    """回环监听；端口由操作系统分配后回读，**代码里不写端口**。"""

    def __init__(self, endpoint_value: str, host: str) -> None:
        self.endpoint_value = endpoint_value
        self._rows: list[Callback] = []
        self._lock = threading.Lock()
        handler = type("_H", (_Handler,), {"sink": self})
        self._srv = http.server.ThreadingHTTPServer((host, 0), handler)
        self._port = self._srv.server_address[1]
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)

    # ── 生命周期 ────────────────────────────────────────────────
    def start(self) -> "HttpListener":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()

    @property
    def port(self) -> int:
        return int(self._port)

    def _record(self, path: str, host: str, ua: str) -> None:
        with self._lock:
            self._rows.append(Callback(path=path, source_host=host, user_agent=ua, at=len(self._rows)))

    # ── OobSink ────────────────────────────────────────────────
    def snapshot(self) -> int:
        with self._lock:
            return len(self._rows)

    def since(self, mark: int) -> list[Callback]:
        with self._lock:
            return list(self._rows[mark:])

    def endpoint(self) -> str:
        return self.endpoint_value


@dataclass(frozen=True)
class OobVerdict:
    hit: bool
    callbacks: tuple[Callback, ...]
    reason: str


class OobProbe:
    """把"有没有回调"收敛成一次判定，judge 只读结果（保持纯函数）。"""

    def __init__(self, sink: OobSink) -> None:
        self.sink = sink

    def mark(self) -> int:
        return self.sink.snapshot()

    def check(self, mark: int, nonce: str, *, self_hosts: tuple[str, ...] = ()) -> OobVerdict:
        rows = tuple(c for c in self.sink.since(mark) if nonce in c.path)
        if not rows:
            return OobVerdict(False, (), "no_callback")
        external = tuple(c for c in rows if c.source_host not in self_hosts)
        if not external:
            return OobVerdict(False, rows, "callback_from_self")
        return OobVerdict(True, external, "callback_observed")


def free_port(host: str) -> int:
    """仅为测试探测：绑 0 号口后立刻释放（host 由调用方给，产品代码不写回环字面量）。"""
    with socket.socket() as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])
