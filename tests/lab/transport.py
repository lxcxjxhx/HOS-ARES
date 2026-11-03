"""WsgiTransport：把 oracle 的发包换成**进程内** WSGI 调用（零 socket）。

真实 HTTP 传输在 B10 接 ``tools.http.send``；本类保持完全相同的调用形状，
所以 oracle 与骨架一行都不用改。这也是"靶场断网可跑"的实现基础。

模板渲染规则（与生产同构）：模板里的 ``{name}`` 用同名参数填；**没被模板吃掉的参数走 query**。
这正好对应契约里的 path 参数与 query 参数，oracle 不需要知道哪个是哪个。

B25：占位符替换覆盖**整条模板**（含 ``?`` 之后那段），与 ``net_transport.render_request``
同一条规则。原来这里直接丢掉 query 段——参数照样走 rest，行为碰巧正确，
但两套传输因此不同构：声明一旦带 ``?q={q}``，真网络版会双写、本版会照旧正常，
分叉只在切换传输时才暴露。
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from veripen_oracles.common.harness import Trace

from .app import App, make_token

_SLOT = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


class WsgiTransport:
    def __init__(self, app: App) -> None:
        self.app = app
        self.sent: list[dict[str, Any]] = []

    def send(
        self,
        binding: Any,
        params: Mapping[str, str],
        role: str,
        headers: Mapping[str, str] | None = None,
    ) -> Trace:
        values = {k: str(v) for k, v in params.items()}
        account = values.pop("__role__", None) or ("primary" if role == "baseline" else "secondary")

        path, fixed_query, consumed = _render(binding.template, values)
        rest = {k: v for k, v in values.items() if k not in consumed}
        tail = "&".join(f"{k}={v}" for k, v in rest.items())

        environ: dict[str, Any] = {
            "REQUEST_METHOD": getattr(binding, "method", "GET"),
            "PATH_INFO": path,
            "QUERY_STRING": "&".join(p for p in (fixed_query, tail) if p),
        }
        token = (headers or {}).get("Authorization") or ("Bearer " + make_token(account))
        environ["HTTP_AUTHORIZATION"] = token
        # 其余 header 一律进 environ（Origin / x-vp-gql 等），
        # 生产侧由真实 HTTP 栈做同样的事；这里是为了让靶场与生产同构。
        for name, value in (headers or {}).items():
            if name.lower() == "authorization":
                continue
            environ["HTTP_" + name.upper().replace("-", "_")] = str(value)

        captured: dict[str, Any] = {}

        def start_response(status: str, hdrs: list[tuple[str, str]]) -> None:
            captured["status"] = int(status.split()[0])
            captured["headers"] = dict(hdrs)

        body = b"".join(self.app(environ, start_response))
        self.sent.append(
            {"role": role, "account": account, "path": path, "query": environ["QUERY_STRING"]}
        )
        return Trace(
            route_ref=getattr(binding, "ref", ""),
            status=int(captured.get("status", 0)),
            body=body,
            headers=dict(captured.get("headers", {})),
        )


def _render(template: str, values: Mapping[str, str]) -> tuple[str, str, set[str]]:
    """填占位符 → (path, 模板自带的 query, 已消费的参数名)。

    占位符在**整条模板**上替换，再拆 path / query——与真网络版同规则。
    拆之前替换才能让 ``?q={q}`` 里的 ``{q}`` 被吃掉，否则它既是"未替换的占位符"
    又是"未被消费的参数"，两条路都会漏。
    """
    consumed: set[str] = set()

    def sub(m: re.Match[str]) -> str:
        name = m.group(1)
        if name in values:
            consumed.add(name)
            return values[name]
        return m.group(0)

    filled = _SLOT.sub(sub, template)
    path, _, fixed_query = filled.partition("?")
    return path, fixed_query, consumed
