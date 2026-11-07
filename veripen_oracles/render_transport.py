"""给 oracle 一条**浏览器**传输（B116）。

为什么需要：DOM XSS 的判定依据是"脚本有没有真跑"，HTTP 响应里看不出来。
所以把浏览器包成和 ``CoreTransport`` 同形的 ``send()``：oracle 仍然只做
plan/judge，判定输入是 ``Trace.body``——只不这里面的 body 是**渲染后**的观测：

    {"marker": "1", "outcome": "ok", "dom_sha256": "…", "path": "<面模板>"}

``marker`` 是页面脚本执行与否的唯一证据：载荷写 ``window.__vp_xss=1``，
渲染完读 ``window.__vp_xss``——没执行就是空串。

边界：
- **地址不进 trace**：``Trace`` 里只有 ``route_ref`` 与 path，host 不落盘；
- 闸门照走（``binding_gate`` + ``preflight``）；浏览器自己发起的连接拿不到 peer ip，
  所以 ``post_connect`` 这一步在这里是缺的——写在这里，不假装有。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from veripen_core.routes.types import RouteBinding
from veripen_core.tools import http as http_tool

from .common.harness import Trace
from .live import GuardMissing

#: 默认标记表达式：载荷把 ``window.__vp_xss`` 置位，只有真执行才会是 1。
MARKER_JS = 'window["__vp_xss"] === undefined ? "" : String(window["__vp_xss"])'

#: 缺省端口不写进 URL（写进去会让同一个面有两个样子）。
_DEFAULT_PORTS = {"http": 80, "https": 443}


class RenderTransport:
    """oracle 视角的"渲染传输"：``send(binding, params, role, headers) -> Trace``。"""

    def __init__(
        self,
        renderer: Any,
        *,
        oracle_id: str = "",
        marker: str = MARKER_JS,
        timeout_s: float = 20.0,
    ) -> None:
        self.renderer = renderer
        self.oracle_id = oracle_id
        self.marker = marker
        self.timeout_s = timeout_s
        self.sent: list[dict[str, object]] = []

    @staticmethod
    def _with_query(binding: RouteBinding, params: dict[str, str], path: str) -> str:
        """模板没消费的参数补成 query——否则载荷发不出去，判定会假 REFUTED。"""
        template = str(getattr(binding, "template", "") or "")
        rest = {k: v for k, v in params.items() if "{" + k + "}" not in template}
        if not rest:
            return path
        sep = "&" if "?" in path else "?"
        # quote 而不是 urlencode：后者把空格编成 +，前端 decodeURIComponent 不会还原，
        # 载荷里的空格会变成字面量 +（属性名损坏 -> 永不执行 -> 假 REFUTED）
        from urllib.parse import quote
        pair = "&".join(f"{k}={quote(str(v), safe="")}" for k, v in rest.items())
        return path + sep + pair

    def send(
        self,
        binding: RouteBinding,
        params: Mapping[str, str],
        role: str,
        headers: Mapping[str, str] | None = None,
    ) -> Trace:
        ctx = http_tool.installed_guard()
        if ctx is None:
            raise GuardMissing("未安装 GuardContext：任何出网都要先过闸门")

        ctx.oracle_id = self.oracle_id or ctx.oracle_id
        ctx.binding_gate(binding)   # ← 资源越界就此拒绝
        ctx.preflight()             # ← 预算 / 令牌桶 / 方法

        from veripen_core.tools.transport import render

        r = render(binding, dict(params))
        path = self._with_query(binding, dict(params), r.path)
        netloc = r.host if r.port == _DEFAULT_PORTS.get(r.scheme, r.port) else f"{r.host}:{r.port}"
        url = f"{r.scheme}://{netloc}{path}"

        self.sent.append({"role": role, "path": path})
        cap = self.renderer.capture(url=url, marker=self.marker, timeout_s=int(self.timeout_s))

        body = json.dumps(
            {
                "marker": cap.marker,
                "outcome": cap.outcome,
                "reason": cap.reason,
                "dom_sha256": hashlib.sha256(cap.html.encode("utf-8")).hexdigest()[:16],
                "path": path,
                "channel": cap.channel,
                # 逐参数：这个值有没有**原样**出现在网络原始响应里。
                # 传输层只知道"发了什么、回来什么"，不猜它意味着什么。
                "echoed": {
                    k: bool(v) and (v in cap.raw_html) for k, v in params.items()
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
        return Trace(
            route_ref=str(getattr(binding, "ref", "")),
            status=200 if cap.outcome == "ok" else 0,
            body=body,
            headers={},
            scheme=r.scheme,
            error=None if cap.outcome == "ok" else (cap.reason or "render_failed"),
        )


__all__ = ["MARKER_JS", "RenderTransport"]
