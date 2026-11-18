"""B5 靶场配套：把 OOB 命中注入 trace，并给"三档难度"用例准备夹具。

``LabTransport`` 继承进程内 WSGI 传输，额外做一件事：**每次 send 前后做 OOB 快照差**，
命中本变体的 nonce 就在 trace 头上打 ``oob_seen=1``。这样 judge 仍然只读 trace（纯函数）。
"""

from __future__ import annotations

from typing import Any, Mapping

from .app import App
from .transport import WsgiTransport

from hos_ares_oracles.common.harness import Trace
from hos_ares_oracles.oob import LabOob


from hos_ares_oracles.oob import LabOob, hit_headers as _hit_headers


def oob_headers(oob: LabOob, mark: int, params: Mapping[str, str]) -> dict[str, str]:
    """B383：规则挪进产品（`hos_ares_oracles.oob.hit_headers`），这里只做转发。

    以前这条规则只活在靶场里，产品侧的 transport 完全不知道 OOB ——
    "同一份 oracle 在两种传输下判定必须一致"这句话就是空的。
    """
    return _hit_headers(oob, mark, params)


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
