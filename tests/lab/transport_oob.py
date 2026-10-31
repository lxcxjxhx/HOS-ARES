"""B5 靶场配套：把 OOB 命中注入 trace，并给"三档难度"用例准备夹具。

``LabTransport`` 继承进程内 WSGI 传输，额外做一件事：**每次 send 前后做 OOB 快照差**，
命中本变体的 nonce 就在 trace 头上打 ``oob_seen=1``。这样 judge 仍然只读 trace（纯函数）。
"""

from __future__ import annotations

from typing import Any, Mapping

from .app import App
from .transport import WsgiTransport

from veripen_oracles.common.harness import Trace
from veripen_oracles.oob import LabOob


class LabTransport(WsgiTransport):
    def __init__(self, app: App, oob: LabOob | None = None) -> None:
        super().__init__(app)
        self.oob = oob or LabOob(app=app, endpoint_value="oob-SAMPLE")

    def send(
        self,
        binding: Any,
        params: Mapping[str, str],
        role: str,
        headers: Mapping[str, str] | None = None,
    ) -> Trace:
        mark = self.oob.snapshot()
        trace = super().send(binding, params, role, headers)
        last_url = next((str(v) for k, v in params.items() if k not in ("__role__",)), "")
        payload = last_url.rsplit("/", 1)[-1]
        rows = [c for c in self.oob.since(mark) if payload and payload in c.path]
        out_headers = dict(trace.headers)
        if rows:
            out_headers["oob_seen"] = "1"
        return Trace(
            route_ref=trace.route_ref,
            status=trace.status,
            body=trace.body,
            headers=out_headers,
        )
