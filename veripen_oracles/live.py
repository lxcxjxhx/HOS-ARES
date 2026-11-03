"""把 oracle 的 ``send`` 接到**生产守卫链**上（D-05 选项 B 的传输层）。

为什么需要这一层：``tools/http.send`` 是产品路径，但它返回的 ``HttpTrace``
**只保留 body 的 sha256**（05 §7 数据分级：正文不落证据）。oracle 判定要看正文，
所以它不能直接用。

这里复用**同一条链**——``binding_gate`` → ``preflight`` → ``render`` →
``perform`` → ``post_connect``——只是把 payload 原样交给 oracle。
闸门一步不少，也没有 ``skip_guard`` 参数。

与靶场两套传输（``tests/lab/transport.py`` / ``net_transport.py``）的分工：

- 那两套跑在**判据自测**上，要的是"同一条用例在不同传输下结论一致"；
- 这一套跑在**真实目标**上，要的是"每次出网都过闸"。

所以它不复用它们的实现：复用会把"测试夹具能跳过守卫"这件事带进生产路径。
"""

from __future__ import annotations

from typing import Callable, Mapping

from .common.harness import Trace

from veripen_core.guard.scope import check_post_connect
from veripen_core.routes.types import RouteBinding
from veripen_core.tools import http as http_tool
from urllib.parse import quote

from veripen_core.tools.transport import RenderResult, TransportError, perform, render

#: 与 ``tools/http.py`` 同值的默认 UA。两处必须一致，否则同一个产品
#: 对目标呈现两种身份——那会让"谁在打"这件事在目标日志里分叉。
USER_AGENT = "veripen/1"


class GuardMissing(RuntimeError):
    """未安装 GuardContext：**拒绝出网**，不降级、不重试。"""

    code = "VP-2001"


class CoreTransport:
    """oracle 视角的传输：形状与靶场两套一致，闸门走真的。

    ``send(binding, params, role, headers) -> harness.Trace``。

    ``role`` 只用于 tokens：``baseline`` 用主身份，其它用第二身份。
    ``token_for(role)`` 由调用方注入——**本类不自己造 token**，
    生产里没人替你补 ``Authorization``。
    """

    def __init__(
        self,
        *,
        token_for: Callable[[str], str] | None = None,
        oob: object | None = None,
        timeout_s: float = 10.0,
        oracle_id: str = "",
    ) -> None:
        self.token_for = token_for
        self.oob = oob
        self.timeout_s = timeout_s
        self.oracle_id = oracle_id
        self.sent: list[dict[str, object]] = []

    def send(
        self,
        binding: RouteBinding,
        params: Mapping[str, str],
        role: str,
        headers: Mapping[str, str] | None = None,
    ) -> Trace:

        ctx = http_tool.installed_guard()
        if ctx is None:
            raise GuardMissing("未安装 GuardContext：任何出网必须先过四处校验")

        ctx.oracle_id = self.oracle_id or ctx.oracle_id
        ctx.binding_gate(binding)   # ② 来源越界即抛
        ctx.preflight()             # ③ 预算 / 令牌桶 / 审批

        r: RenderResult = render(binding, dict(params))

        # ── 未消费的参数走 query ────────────────────────────────────
        #
        # ``tools.transport.render`` 只替换模板里的 ``{name}``，**不会**把剩下的参数
        # 拼进 query。而契约归一后的模板常常只有路径（``/api/search`` + param ``q``），
        # 于是 q 被静默丢掉：目标收到的是一个不带参数的请求，差分消失、正例判成
        # REFUTED——看起来像"目标没漏洞"，实际是我没把参数发出去。
        #
        # 这条与靶场两套传输的规则一致（它们也这么处理），差别只是这里要自己拼。
        via_template = {name for name in params if "{" + name + "}" in binding.template}
        rest = {
            k: v for k, v in params.items()
            if k not in via_template and not str(k).startswith("__")
        }
        if rest:
            tail = "&".join(
                f"{quote(str(k), safe='')}={quote(str(v), safe='')}"
                for k, v in rest.items()
            )
            sep = "&" if "?" in r.path else "?"
            r = RenderResult(
                scheme=r.scheme, host=r.host, port=r.port,
                path=r.path + sep + tail, method=r.method,
                template_sha256=r.template_sha256,
            )

        def _post_connect(peer_ip: str) -> None:
            check_post_connect(
                peer_ip=peer_ip,
                asset=ctx.scope.asset,
                deny_nets=getattr(ctx.scope, "deny_nets", ()),
                explicit_ips=(r.host,),
            )

        hdrs: dict[str, str] = {"User-Agent": USER_AGENT}
        if self.token_for is not None:
            account = "primary" if role == "baseline" else "secondary"
            token = (self.token_for(account) or "").strip()
            # 空 token 不发头：发一个 "Bearer " 出去，目标会把它当**无效凭据**记进日志，
            # 那比不发更糟——它会掩盖"这一次根本没带身份"这件事。
            if token:
                hdrs["Authorization"] = "Bearer " + token
        for name, value in (headers or {}).items():
            hdrs[name] = str(value)

        try:
            status, resp_headers, payload = perform(
                r, headers=hdrs, timeout_s=self.timeout_s, post_connect=_post_connect
            )
        except TransportError as exc:
            self.sent.append({"role": role, "error": str(exc)[:120]})
            return Trace(route_ref=binding.ref, status=0, body=b"", error=str(exc)[:200])

        self.sent.append({"role": role, "status": status, "bytes": len(payload)})
        # HttpTrace 只留 body 哈希，那是给 evidence 层用的形状；oracle 要判正文，
        # 所以这里返回完整的 Trace，不经过 models.HttpTrace。
        return Trace(route_ref=binding.ref, status=status, body=payload,
                     headers=resp_headers)


__all__ = ["CoreTransport", "GuardMissing", "USER_AGENT"]
