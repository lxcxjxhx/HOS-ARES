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


def oob_headers(oob: LabOob, mark: int, params: Mapping[str, str]) -> dict[str, str]:
    """``mark`` 之后新产生的回调里，有本变体 nonce 的 → 打 ``oob_seen``。

    **传输无关**：进程内 WSGI 与真网络都要调它，否则同一份 oracle 在两种传输下
    判定会分叉（P-05/P-06 就这样白丢了两条正样本）。所以这条规则只有一份实现。
    """
    last_url = next((str(v) for k, v in params.items() if k not in ("__role__",)), "")
    payload = last_url.rsplit("/", 1)[-1]
    rows = [c for c in oob.since(mark) if payload and payload in c.path]
    return {"oob_seen": "1"} if rows else {}


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
        method: str = "",
    ) -> Trace:
        mark = self.oob.snapshot()
        trace = super().send(binding, params, role, headers, method=method)
        return Trace(
            route_ref=trace.route_ref,
            status=trace.status,
            body=trace.body,
            headers={**dict(trace.headers), **oob_headers(self.oob, mark, params)},
        )
