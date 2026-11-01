"""WsgiTransport：把 oracle 的发包换成**进程内** WSGI 调用（零 socket）。

真实 HTTP 传输在 B10 接 ``tools.http.send``；本类保持完全相同的调用形状，
所以 oracle 与骨架一行都不用改。这也是"靶场断网可跑"的实现基础。

模板渲染规则（与生产同构）：模板里的 ``{name}`` 用同名参数填；**没被模板吃掉的参数走 query**。
这正好对应契约里的 path 参数与 query 参数，oracle 不需要知道哪个是哪个。
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

        path, consumed = _render(binding.template, values)
        rest = {k: v for k, v in values.items() if k not in consumed}

        environ: dict[str, Any] = {
            "REQUEST_METHOD": getattr(binding, "method", "GET"),
            "PATH_INFO": path,
            "QUERY_STRING": "&".join(f"{k}={v}" for k, v in rest.items()),
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


def _render(template: str, values: Mapping[str, str]) -> tuple[str, set[str]]:
    consumed: set[str] = set()

    def sub(m: re.Match[str]) -> str:
        name = m.group(1)
        if name in values:
            consumed.add(name)
            return values[name]
        return m.group(0)

    return _SLOT.sub(sub, template.split("?", 1)[0]), consumed
