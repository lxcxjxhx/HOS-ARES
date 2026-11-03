"""B20 · `act.http_*` 的真实发送器（`35 §4` A1/A2）。

`actions.http_raw` / `http_fuzz` 一直收一个注入的 ``send`` 回调，
但从来没有人给过它真实现——所以 A10 一直停在 3/6～4/6。

这批补的是 `make_binding_sender` / `make_param_sender`：把「已解析绑定 +
运行期目标解析器」变成能真发包的 send。地址仍然只存在于 `RouteBinding`
与运行期的解析值里，不落任何持久物。

**与 `tools/http.send` 的分工**：那个走完整 guard（绑定门/预算/审批/post_connect），
是产品路径；这个是动作层的轻量发送器，用于 `act.*` 的探索性请求。
两者共用同一套"地址只能来自绑定"的纪律，但闸门深度不同——
动作层产物只能是 lead（I12 不产 verdict），所以不需要 post_connect 那一层。

**沙箱**：本机无 docker / 无 WSL（B20 账本 open 已记），
这里**不声称容器隔离**。出网走宿主网络栈。这一点在 A10/A11 输出里如实标注。
"""

from __future__ import annotations

from typing import Callable, Mapping
from urllib.parse import quote, urlsplit

from ..routes.types import RouteBinding
from ..tools.transport import RenderResult, TransportError, perform


class SenderError(RuntimeError):
    code = "VP-8030"


#: ``send(binding, method, headers, body) -> Mapping``
BindingSender = Callable[[RouteBinding, str, Mapping[str, str], str], Mapping[str, object]]

#: ``send(binding, param, variant) -> Mapping``
ParamSender = Callable[[RouteBinding, str, str], Mapping[str, object]]

#: 值得多看一眼的状态码：200 之外的 3xx/4xx 都可能意味着"绕过了什么"。
_INTERESTING = frozenset({200, 201, 204, 301, 302, 307, 401, 403, 500})


def make_binding_sender(
    *,
    resolve_base: Callable[[str], str],
    timeout_s: float = 10.0,
) -> BindingSender:
    """把绑定渲染成真请求并发出。``resolve_base(route_ref)`` 在运行期给 base。"""

    def _send(
        binding: RouteBinding, method: str, headers: Mapping[str, str], body: str
    ) -> Mapping[str, object]:
        status, payload = _fire(binding, resolve_base, {}, method, headers, body, timeout_s)
        interesting = status in _INTERESTING
        return {
            "status": status,
            "bytes": len(payload),
            "interesting": interesting,
            "verifiable_by": ("open_redirect", "idor_cross_account") if interesting else [],
        }

    return _send


def make_param_sender(
    *,
    resolve_base: Callable[[str], str],
    timeout_s: float = 10.0,
) -> ParamSender:
    """按参数名注入变体并与基线比。**差分靠真响应，不靠猜。**"""

    def _send(binding: RouteBinding, param: str, variant: str) -> Mapping[str, object]:
        if "{" + param + "}" not in binding.template:
            raise SenderError(f"绑定模板没有 {{{param}}}，无法注入（不许自己拼路径）")
        base_status, base_body = _fire(
            binding, resolve_base, {param: "1"}, binding.method, {}, "", timeout_s
        )
        probe_status, probe_body = _fire(
            binding, resolve_base, {param: variant}, binding.method, {}, "", timeout_s
        )
        return {
            "status": probe_status,
            "baseline_status": base_status,
            "differential": probe_status != base_status or probe_body != base_body,
        }

    return _send


def _fire(
    binding: RouteBinding,
    resolve_base: Callable[[str], str],
    params: Mapping[str, str],
    method: str,
    headers: Mapping[str, str],
    body: str,
    timeout_s: float,
) -> tuple[int, bytes]:
    """渲染 → 发 → 返回 (status, body)。目标字符串只在本调用栈里存在。

    地址有两种合法来源，**按模板形态二选一**（B24）：

    - **相对模板**（``/api/ping``）：host 由 ``resolve_base(binding.ref)`` 在运行期给；
    - **绝对模板**（自带 scheme 与 host）：host 来自模板本身——路由层
      （``guard/scope.py`` 的 ``_host_registered``）已经承认「模板可能带绝对前缀」，
      这里对齐它，而不是把绝对模板当相对路径补前导斜杠、静默打成 404。

    两者都不落任何持久物：``rendered`` / ``base`` 都是本调用栈的局部值。
    """
    rendered = _render_relpath(binding.template, params)
    if "://" in rendered:
        own = urlsplit(rendered)
        if not own.hostname:
            raise TransportError(f"绝对模板没有主机名：{rendered[:40]!r}")
        scheme, host, port = own.scheme or "http", own.hostname, own.port
        path = (own.path or chr(0x2F)) + (f"?{own.query}" if own.query else "")
    else:
        base = resolve_base(binding.ref)
        parts = urlsplit(base)
        if not parts.hostname:
            raise TransportError(f"解析基底没有主机名：{base[:40]!r}")
        scheme, host, port = parts.scheme or "http", parts.hostname, parts.port
        path = rendered
    target = render_target(scheme, host, port, path,
                           method or binding.method, binding.template_sha256)
    status, _resp_headers, payload = perform(
        target, headers=dict(headers), body=body.encode("utf-8") or None, timeout_s=timeout_s
    )
    return status, payload


def _render_relpath(template: str, params: Mapping[str, str]) -> str:
    """填占位符 → 相对路径或完整 URL，**原样返回形态**（B24）。

    **不用 ``transport.render``**：那个要求完整 URL 并自己拆 host/port，
    而动作层还要区分「host 来自模板」与「host 来自运行期解析基底」两种来源，
    混在一起就分不清地址是谁给的。

    绝对模板（含 ``://``）原样返回，交给调用方按"模板自带 host"处理；
    相对模板补前导 ``/``。这条分叉必须显式——旧实现把绝对模板当相对路径
    加了个前导斜杠，请求行随即变成非法路径，服务端只会回 404。
    """
    filled = template
    for name, value in params.items():
        filled = filled.replace("{" + name + "}", quote(str(value), safe=""))
    if "{" in filled and "}" in filled:
        remaining = [seg for seg in filled.split("{") if "}" in seg]
        raise TransportError(f"模板仍有未替换参数：{remaining}")
    if "://" in filled:
        return filled
    return filled if filled.startswith(chr(0x2F)) else chr(0x2F) + filled


def render_target(scheme: str, host: str, port: int | None, path: str, method: str,
                  template_sha256: str) -> "RenderResult":
    """构造 `RenderResult`（transport 层的输入类型）。**工厂函数，不在别处 new。**"""
    from_default = 443 if scheme == "https" else 80
    return RenderResult(
        scheme=scheme,
        host=host,
        port=port or from_default,
        path=path,
        method=method.upper(),
        template_sha256=template_sha256,
    )


__all__ = [
    "BindingSender",
    "ParamSender",
    "SenderError",
    "make_binding_sender",
    "make_param_sender",
    "render_target",
]
