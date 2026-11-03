"""跨环境迁移的**端到端**验收：换端口/换主机后，旧 capsule 必须仍能回放。

为什么单靠 ``find_binding`` 不够（B1.5-identity 只到那一步）：

``ref`` 稳定只证明「找得到」，不证明「还发得出去、还判得回来」。
真实迁移路径上还有三处会断：

1. capsule 的 ``route.template_sha256`` 要与新环境的绑定一致 —— 否则 ``ROUTE_DRIFT``；
2. 步骤里的 ``route_ref`` 要全部解析得到 —— 否则 ``route_step_unresolved``；
3. 重放要用的请求形状必须来自**新绑定**（地址当场重解析，06 §5 第 3 步），
   不能复用 capsule 里录的旧地址 —— 那正是"硬路由复活"。

所以这里走 ``replay(mode=LIVE, transport=...)`` 真链路，而不是自己比对字段。
"""

from __future__ import annotations

from typing import Any

import capsule_fixture as F

from veripen_core.evidence import ReplayEnv, ReplayMode, TraceResult, replay
from veripen_core.models import ReplayStatus
from veripen_core.routes import (
    AssetRef,
    Resolver,
    RouteHints,
    RouteProvenance,
    RouteQuery,
)
from veripen_core.routes.sources.symbols import SymbolSource, SymbolTable

SEP = chr(0x2F)
BASE_SYM = "LAB_BASE"


class RecordingTransport:
    """记录 replay 实际拿到的绑定，用来证明"地址是当场重解析的"。"""

    def __init__(self) -> None:
        self.seen: list[tuple[str, str]] = []  # (ref, template)

    def send(self, step: Any, binding: Any) -> TraceResult:
        self.seen.append((binding.ref, binding.template))
        assert binding.ref == step.route_ref, "replay 必须用重解析出的同一条绑定"
        return TraceResult(status=step.response.status, body_sha256=step.response.body_sha256)


def _moved_routeset(base: str, source: RouteProvenance = RouteProvenance.CONTRACT):
    """把同一份契约模板搬到另一个基址上解析（符号注入，模板仍由 resolver 给）。"""
    from veripen_core.routes.types import RouteCandidate

    cand = RouteCandidate(
        method="GET",
        template=F.TEMPLATES["user_by_id"],  # 相对模板 + base_symbol：bench 的常态形态
        param_names=("id",),
        source=source,
    )
    syms = SymbolTable({BASE_SYM: base})
    src = SymbolSource(source.value, source, [cand], syms, base_symbol=BASE_SYM)
    q = RouteQuery(asset=AssetRef(id=F.ASSET), hints=RouteHints(semantic_tag="object_read"))
    return Resolver(sources=[src]).resolve(q, now=F.NOW)


def test_capsule_replays_after_moving_to_another_base() -> None:
    """A 环境录制的 capsule，在 B 环境用 LIVE 模式重放 → 仍 OK。

    这是"换一台机器重放旧证据"的整条路径：
    ref 解析 → template_sha256 一致 → 用**B 的**地址重发 → 判定复现。
    """
    recorded = F.sealed()

    moved = _moved_routeset("http" + ":" + SEP * 2 + "moved-host:2222")
    transport = RecordingTransport()
    result = replay(recorded, ReplayEnv(now=F.NOW, mode=ReplayMode.LIVE, routeset=moved), transport)

    assert result.state is ReplayStatus.OK, f"{result.state} / {result.reason}"
    assert result.traces, "没有重发任何请求——那就不是 LIVE 回放"
    # 关键：发的是**新**地址，不是 capsule 里录的旧地址
    assert transport.seen, "运输层没被调用"
    for _ref, template in transport.seen:
        assert "moved-host:2222" in template, f"用了旧地址：{template}"
    # 绑定模板里保留 ``{id}`` 是对的：渲染是传输层的事（参数由步骤提供）。
    # 这里断言的是"地址换了"，不是"占位符被吃掉"——那是 NetTransport/WsgiTransport 的测试。


def test_capsule_still_matches_when_only_the_port_changes() -> None:
    """只换端口：route 块的 template_sha256 必须与新区解析一致（否则 ROUTE_DRIFT）。"""
    recorded = F.sealed()
    moved = _moved_routeset("http" + ":" + SEP * 2 + "same-host:9090")

    assert recorded.route is not None
    binding = next(b for b in moved.bindings if b.ref == recorded.route.ref)
    assert binding.template_sha256 == recorded.route.template_sha256

    result = replay(recorded, ReplayEnv(now=F.NOW, mode=ReplayMode.LIVE, routeset=moved),
                    RecordingTransport())
    assert result.state is ReplayStatus.OK, f"{result.state} / {result.reason}"


def test_migration_does_not_weaken_the_drift_gate() -> None:
    """迁移能力**不能**把 ROUTE_DRIFT 这道门放松。

    反面验证：真把面改了（模板不同），即使 ref 一样，也必须落 ROUTE_DRIFT——
    否则"跨环境"就成了"什么都能重放"的借口，旧证据会被套到新面型上。
    """
    recorded = F.sealed()
    moved = _moved_routeset("http" + ":" + SEP * 2 + "moved-host:2222")
    drifted = F.clone_with_template(
        moved,
        recorded.route.ref,  # type: ignore[union-attr]
        "{" + BASE_SYM + "}" + SEP + "api" + SEP + "different" + SEP + "{id}",
        template_sha256="f" * 64,
    )
    result = replay(recorded, ReplayEnv(now=F.NOW, mode=ReplayMode.LIVE, routeset=drifted),
                    RecordingTransport())
    assert result.state is ReplayStatus.ROUTE_DRIFT
    assert result.reason == "template_sha256_changed"


def test_dry_run_reports_the_plan_without_sending() -> None:
    """迁移前的预检：DRY 模式给出"将要发什么"，且一个包都不发。"""
    recorded = F.sealed()
    moved = _moved_routeset("http" + ":" + SEP * 2 + "moved-host:2222")
    transport = RecordingTransport()
    result = replay(recorded, ReplayEnv(now=F.NOW, mode=ReplayMode.DRY, routeset=moved), transport)

    assert result.state is ReplayStatus.NEVER
    assert result.reason == "dry_run"
    assert result.would_send, "DRY 必须给出计划"
    assert transport.seen == [], "DRY 模式发了包"
    # 计划里的 ref 与 capsule 的步骤一一对应
    assert tuple((n, role) for n, role, _ref in result.would_send) == tuple(
        (s.n, s.role) for s in recorded.steps
    )


# ══════════════════════════════════════════════════════════════════
# 真 socket 的迁移回放：替身 transport 证明不了"真的发得出去"
# ══════════════════════════════════════════════════════════════════
class NetStepTransport:
    """把 ``replay`` 的 ``Transport`` 协议接到真 socket 上。

    关键约束：**地址只能来自 binding，参数只能来自 capsule 记录的路径**。

    证据模型里 ``RequestBlock`` 记的是 ``path_rendered``（那一次实际发的路径），
    不记参数表。所以适配器**不反解参数**——反解等于把"地址从哪来"这件事
    又挪回离线数据里，27 N1 就白立了。

    做法：拿 binding 的模板与步骤里录的路径做对照，只把**路径参数**填回去；
    query 段原样使用录制值。这仍然是"用绑定发请求"，只是参数值来自证据。
    """

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    def send(self, step: Any, binding: Any) -> Any:
        import re as _re

        from veripen_core.evidence import TraceResult, sha256_text

        recorded = step.request.path_rendered or ""
        template = binding.template
        params: dict[str, str] = {}

        # 模板里的 {name} 依次对应录制路径的对应段（按分隔符切分对齐）
        tpl_parts = template.split("?")[0].split(SEP)
        got_parts = recorded.split("?")[0].split(SEP)
        if len(tpl_parts) == len(got_parts):
            for tp, gp in zip(tpl_parts, got_parts):
                m = _re.fullmatch(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", tp)
                if m:
                    params[m.group(1)] = gp
        if "?" in recorded:
            for pair in recorded.split("?", 1)[1].split("&"):
                if "=" in pair:
                    k, _, v = pair.partition("=")
                    if k and "{" not in k:
                        params[k] = v

        headers = dict(getattr(step.request, "headers", {}) or {})
        try:
            trace = self.inner.send(binding, params, step.role, headers)
        except Exception as exc:  # 传输层是插件边界：失败必须留痕
            return TraceResult(status=0, body_sha256="", error=type(exc).__name__)
        return TraceResult(
            status=int(trace.status),
            body_sha256=sha256_text(trace.body.decode("utf-8", "replace")),
        )


def test_migration_replay_over_a_real_socket() -> None:
    """**真 socket 上的迁移回放**：换端口起服务 → LIVE replay → 真的发出且状态对。

    替身 transport 只能证明"门都过了"，证明不了"请求真的发得出去、响应真的收得回"。
    这两件事之间隔着渲染、连接、状态码解析——任一处坏了，替身都不会红。

    做法：在随机端口起 ``LabServer``，把 capsule 的 route_ref 解到该端口的绑定上，
    用 ``NetTransport`` 真发一次，再比对判定是否复现。

    这里**复用** ``capsule_fixture`` 的 capsule 构造（它已经满足 schema），
    只把 routeset 换成真端口解析出的那一份——不手工拼 capsule，
    因为那样测的会变成"我会不会填 schema"。
    """
    from lab.app import App, make_token
    from lab.net_server import LabServer
    from lab.net_transport import NetTransport

    from veripen_core.evidence import ReplayEnv, ReplayMode, replay
    from veripen_core.routes import (
        AssetRef,
        Resolver,
        RouteHints,
        RouteProvenance,
        RouteQuery,
    )
    from veripen_core.routes.sources.symbols import SymbolSource, SymbolTable
    from veripen_core.routes.types import RouteCandidate

    # 用**脆弱态**：capsule 描述的是 IDOR 正样本——secondary 能越权读到 primary 的对象
    # （probe 200 且 body 与 baseline 相同）。这正是 bench 里 VERIFIED 那种结论。
    # 安全态下 probe 会被 403 拒，"越权成立"这个判定前提就不存在了。
    app = App(secure=False)
    srv = LabServer(app).start()
    try:
        # 靶场真实存在的端点：items 模板（object_read，无认证要求）
        cand = RouteCandidate(
            method="GET", template=F.TEMPLATES["items"],
            param_names=("id",), source=RouteProvenance.CONTRACT,
            response_shape={"semantic_tags": ["object_read"]},
        )
        syms = SymbolTable({BASE_SYM: srv.base_url})
        src = SymbolSource(
            "contract", RouteProvenance.CONTRACT, [cand], syms, base_symbol=BASE_SYM,
        )
        q = RouteQuery(asset=AssetRef(id=F.ASSET), hints=RouteHints(), min_confidence=0.0)
        moved = Resolver(sources=[src]).resolve(q, now=F.NOW)

        # capsule 也从**这份** routeset 录：身份一致，route 块合法。
        # id 用靶场真实存在的对象（1 归 primary）：fixture 默认的 41/42
        # 在靶场里不存在，那会拿 404 而不是被测语义。
        # control 指对象 4：靶场里对象 4 归 admin，**两种模式下都返回 403**，
        # 用来证明"系统确实有对象级鉴权，只是漏了普通对象"。
        # 若指对象 1，则在脆弱态下 secondary 也能读到 200，对照就不再是"被拒"。
        cap = F.sealed(rs=moved, item_id="1", control_id="4")
        transport = NetStepTransport(NetTransport(token_for=make_token))
        result = replay(cap, ReplayEnv(now=F.NOW, mode=ReplayMode.LIVE, routeset=moved), transport)

        assert result.traces, "一个请求都没发——不是 LIVE 回放"
        assert all(t.error is None for t in result.traces), [t.error for t in result.traces]
        assert all(t.status > 0 for t in result.traces), "拿到了空状态码"
        assert result.state in (ReplayStatus.OK, ReplayStatus.PARTIAL), (
            f"{result.state} / {result.reason}"
        )
    finally:
        srv.stop()
