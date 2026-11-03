"""真网络传输（bench 用）：把 oracle 的请求形状翻译成真 HTTP。

与 ``WsgiTransport`` 的两处**有意不同**，都写在代码里而不是藏在结论里：

1. **不自动造 token**：生产里没人替你补 ``Authorization``。
   bench 切换时必须显式按角色带 token——本模块提供 ``token_for(role)``。
2. **真有连接**：WSGI 版没有连接概念。若某 oracle 依赖它，判定会变，
   那正是「切换」这件事要暴露的东西。

``__role__`` 的处理与 WSGI 版同语义（它由 harness 的 ``Variant.account`` 注入，
表示"这一次用谁的号"），只是这里把它换成那个账号的 token，而不是塞进 environ。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.parse import quote, unquote, urlsplit

from veripen_oracles.common.harness import Trace

_SLOT = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
#: 形如 ``%2e`` 的**已有编码**：传输层必须原样送出，不能二次编码。
_ENCODED = re.compile(r"%[0-9A-Fa-f]{2}")
DEFAULT_PORTS = {"http": 80, "https": 443}


def _encode(value: str) -> str:
    """值进 URI 前的转义。**两部分职责，别混。**

    1. 已经有 ``%XX`` 的**保持原样**：``{{depth_pad_encoded}}`` 这类载荷本身
       就是编码态，再套一层 ``%25`` 会把它变成字面量 ``%2e%2e%2f``，
       服务端解码一次后拿到的仍是字面量 —— 攻击语义被悄悄改掉了。
       WSGI 版不编码所以看不出这个差，这正是"换传输"要暴露的东西。
    2. 其余字符照常转义（空格、``&``、``=`` 等），否则请求行非法、
       ``http.client`` 会直接抛。

    若 ``%`` 后面不是两位十六进制（不是合法编码），退回全量转义——
    宁可多编，也不发一个协议上非法的东西出去。
    """
    # 去掉所有合法的 %XX 之后还有 % → 说明有裸百分号，不能保留
    keep_percent = "%" not in _ENCODED.sub("", value)
    return quote(value, safe="%" if keep_percent else "")


@dataclass(frozen=True)
class Rendered:
    method: str
    path: str
    query: str
    consumed: frozenset[str]


def render_request(binding: Any, values: Mapping[str, str]) -> Rendered:
    """与 WSGI 版**同规则**：模板占位符吃掉同名参数，其余走 query。

    两处**必须不同**，都来自真 HTTP 的语法约束：

    1. 值要转义（空格、``&`` 会让请求行非法），但**已编码的 ``%XX`` 不二次编码**
       —— 见 ``_encode``：那是载荷语义，不是传输细节。
    2. path 与 query 分开转义：模板自带的 ``..`` 属于 path 语义，要留着。

    WSGI 版把 ``q=widget AND 1=1`` 直接塞进 environ 的 ``QUERY_STRING``，
    进程内没有协议校验所以能跑；这不是"真网络更严格"，是
    **WSGI 版一直在发非法请求而我们没发现**。
    """
    consumed: set[str] = set()

    def sub(m: re.Match[str]) -> str:
        name = m.group(1)
        if name in values:
            consumed.add(name)
            return _encode(values[name])
        return m.group(0)

    # ★ 占位符替换必须覆盖**整条模板**，包括 ``?`` 之后那段。
    #
    # 这里原来只替换 path 段（``partition("?")`` 之后的 fixed_query 原样保留），
    # 于是声明成 ``/api/search?q={q}`` 的绑定会发出两段 query：
    # ``q={q}&q=widget`` —— 服务端取第一个值，拿到的是**字面量** ``{q}``，
    # 差分随即消失。WsgiTransport 那边因为直接丢掉 query 段而看不出这个差，
    # 两套传输因此长期不同语义。声明带显式占位符之后这个隐藏的裂缝才露出来。
    filled = _SLOT.sub(sub, binding.template)
    path, _, fixed_query = filled.partition("?")
    rest = {k: v for k, v in values.items() if k not in consumed and not k.startswith("__")}
    encoded = "&".join(f"{quote(k, safe='')}={_encode(v)}" for k, v in rest.items())
    parts = [q for q in (fixed_query, encoded) if q]
    return Rendered(
        method=str(binding.method), path=unquote(path), query="&".join(parts),
        consumed=frozenset(consumed),
    )


class NetTransport:
    """经 ``tools.transport`` 真发请求。形状与 ``WsgiTransport`` 完全一致。

    ``oob`` 与 WSGI 版同源：没有它，``ssrf_oob`` 在真网络下**永远打不上**
    ``oob_seen``，正样本会集体退化成 REFUTED（P-05/P-06 就是这么丢的）。
    回调登记的判定规则在 ``transport_oob.oob_headers`` 里只有一份实现。
    """

    def __init__(
        self,
        *,
        token_for: Callable[[str], str] | None = None,
        timeout_s: float = 5.0,
        oob: Any = None,
    ) -> None:
        self.sent: list[dict[str, Any]] = []
        self._token_for = token_for
        self.timeout_s = timeout_s
        self.oob = oob

    def send(
        self,
        binding: Any,
        params: Mapping[str, str],
        role: str,
        headers: Mapping[str, str] | None = None,
    ) -> Trace:
        from veripen_core.tools import transport as T

        from .transport_oob import oob_headers

        values = {k: str(v) for k, v in params.items()}
        account = values.pop("__role__", None) or ("primary" if role == "baseline" else "secondary")

        r = render_request(binding, values)
        parts = urlsplit(binding.template.split("?", 1)[0].replace("{", "").replace("}", ""))
        scheme = (parts.scheme or "http").lower()
        rr = T.RenderResult(
            scheme=scheme,
            host=parts.hostname or "",
            port=parts.port or DEFAULT_PORTS.get(scheme, 80),
            path=r.path + (("?" + r.query) if r.query else ""),
            method=r.method.upper(),
            template_sha256=getattr(binding, "template_sha256", ""),
        )

        hdrs: dict[str, str] = dict(headers or {})
        if "Authorization" not in hdrs and self._token_for is not None:
            # 显式补 token：这是 bench 的**便利**，不是传输层的默认行为
            hdrs["Authorization"] = "Bearer " + self._token_for(account)

        mark = self.oob.snapshot() if self.oob is not None else 0
        status, resp_headers, payload = T.perform(rr, headers=hdrs, timeout_s=self.timeout_s)
        self.sent.append({"role": role, "account": account, "path": r.path,
                          "query": r.query, "status": status, "params": dict(values)})
        out_headers = dict(resp_headers)
        if self.oob is not None:
            out_headers.update(oob_headers(self.oob, mark, params))
        return Trace(
            route_ref=getattr(binding, "ref", ""),
            status=status,
            body=payload,
            headers=out_headers,
        )



__all__ = ["DEFAULT_PORTS", "NetTransport", "Rendered", "render_request"]
