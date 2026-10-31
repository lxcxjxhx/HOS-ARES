"""证据层（B4）：capsule v1.1、哈希链、六态 replay（06 §2–§5、27 §5.2）。

三条纪律：
1. **内容寻址**：``self_sha256 = sha256(canonical_json(payload))``，payload 去掉
   ``chain.self_sha256`` 与 ``steps[*].request.path_rendered``。同输入两次 seal 字节相同。
2. **只增不改**：seal 之后任何字段变动都被 ``verify_self_sha`` 抓到（VP-5001）。
3. **replay 顺序不可交换**：完整性 → 授权 → 路由 → 重发重判（06 §5.1.1）。
   把"重发"提到"授权"前 = 越界；提到"路由"前 = 把"面没了"写成"已修复"。

本模块不做网络：live 模式的发包由注入的 ``Transport`` 完成，离线模式只调纯函数。
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from .models import ReplayStatus, Verdict, canonical_json, sha256_hex
from .routes.types import RouteBinding, RouteSet

CAPSULE_VERSION = "1.1"
LEGACY_VERSION = "1.0"
ZERO_SHA = "0" * 64
REDACTED = "<redacted>"
SENSITIVE_HEADERS = ("cookie", "authorization", "x-api-key")

_FROZEN = ConfigDict(frozen=True, extra="forbid")


# ── 错误（24 §4 的 5xxx 段）─────────────────────────────────────────
class CapsuleError(RuntimeError):
    code = "VP-5000"


class CapsuleTampered(CapsuleError):
    """内容与 self_sha256 不符（06 §5 步骤 1）。"""

    code = "VP-5001"


class CapsuleIncomplete(CapsuleError):
    """schema 1.1 缺 route 块 / 缺 capsule 文件（06 §5 步骤 1、09 §7）。"""

    code = "VP-5002"


class ChainBroken(CapsuleError):
    """哈希链断裂（``verify-chain`` 定位到具体下标）。"""

    code = "VP-5003"


# ── capsule 文档模型（06 §2 逐字段对应）─────────────────────────────
class OracleBlock(BaseModel):
    model_config = _FROZEN
    id: str
    version: str
    cwe: str = ""
    params_hash: str = ""


class RouteBlock(BaseModel):
    """地址从哪解析来的（27 §5.2）。**不含明文模板**，只有 ``template_sha256``。"""

    model_config = _FROZEN
    routeset_sha256: str
    ref: str
    source: str
    sources_all: list[str] = Field(default_factory=list)
    confidence: float
    template_sha256: str
    resolver_version: str
    resolved_at: str
    ttl_s: int
    discovery_evidence_ids: list[str] = Field(default_factory=list)


class TargetBlock(BaseModel):
    model_config = _FROZEN
    asset_id: str
    frame_method: str = "GET"
    host_key: str = ""
    resolved_ip: str | None = None
    template_sha256: str = ""
    userinfo_rejected: bool = True


class ClockBlock(BaseModel):
    model_config = _FROZEN
    t0: int
    t1: int
    timezone: str = "UTC"


class EgressBlock(BaseModel):
    model_config = _FROZEN
    mode: str = "whitelist"
    proxy: str | None = None


class EnvBlock(BaseModel):
    model_config = _FROZEN
    profile_name: str
    profile_hash: str
    image_digest: str = ""
    runner_id: str = ""
    clock: ClockBlock
    egress: EgressBlock = Field(default_factory=lambda: EgressBlock())
    sandbox_overridden: bool = False


class RequestBlock(BaseModel):
    model_config = _FROZEN
    method: str
    path_rendered_sha256: str
    #: 本地 replay 需要；**不进哈希**，导出时置 None（06 §2.2）
    path_rendered: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = None
    body_sha256: str | None = None


class ResponseBlock(BaseModel):
    model_config = _FROZEN
    status: int
    length: int = 0
    content_type: str = ""
    body_sha256: str = ""
    body_excerpt_b64: str | None = None
    excerpt_truncated: bool = False
    persisted_ref: str | None = None
    persisted_size: int | None = None
    return_code_semantics: str = "ok"


class Step(BaseModel):
    model_config = _FROZEN
    n: int
    role: str
    account: str = ""
    route_ref: str
    request: RequestBlock
    response: ResponseBlock
    peer_ip: str | None = None
    peer_ip_sha256: str = ""
    elapsed_ms: int = 0
    err: str | None = None
    note: str | None = None


class AssertionBlock(BaseModel):
    model_config = _FROZEN
    kind: str
    statement: str = ""
    expected: dict[str, list[int]] = Field(default_factory=dict)
    observed: dict[str, Any] = Field(default_factory=dict)
    fingerprint: dict[str, bool] = Field(default_factory=dict)


class RedactionBlock(BaseModel):
    model_config = _FROZEN
    policy: str = "pii-min"
    fields: list[str] = Field(default_factory=list)
    secrets_in_vault: list[str] = Field(default_factory=list)


class Chain(BaseModel):
    model_config = _FROZEN
    prev_sha256: str | None = None
    self_sha256: str = ""


class CapsuleDoc(BaseModel):
    """capsule 1.1。``route`` 可空仅为读 1.0 旧文件（渲染标"无路由证据"）。"""

    model_config = _FROZEN
    capsule_version: str = CAPSULE_VERSION
    finding_id: str
    created_at: str
    oracle: OracleBlock
    route: RouteBlock | None = None
    target: TargetBlock
    env: EnvBlock
    steps: list[Step]
    assertion: AssertionBlock
    verdict: Verdict
    confidence: float = 0.0
    confidence_rule: str = ""
    route_strength: str = ""
    redaction: RedactionBlock = Field(default_factory=lambda: RedactionBlock())
    chain: Chain = Field(default_factory=lambda: Chain())

    # ── 哈希 ────────────────────────────────────────────────────
    def hash_payload(self) -> dict[str, Any]:
        """进哈希的内容：去 ``chain.self_sha256`` 与 ``path_rendered``（06 §3）。"""
        data: dict[str, Any] = self.model_dump(mode="json")
        chain = data.get("chain")
        if isinstance(chain, dict):
            chain.pop("self_sha256", None)
        for step in data.get("steps", []):
            request = step.get("request")
            if isinstance(request, dict):
                request.pop("path_rendered", None)
        return data

    @property
    def capsule_id(self) -> str:
        return f"sha256:{self.chain.self_sha256}" if self.chain.self_sha256 else ""


# ── 构造与脱敏 ──────────────────────────────────────────────────────
def sha256_text(text: str) -> str:
    return sha256_hex(text.encode("utf-8"))


def redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """凭据只留名字与 ``<redacted>``，值不落盘（06 §4）。"""
    return {
        name: (REDACTED if name.lower() in SENSITIVE_HEADERS else value)
        for name, value in headers.items()
    }


def build_request(
    *,
    method: str,
    path_rendered: str,
    headers: Mapping[str, str] | None = None,
    body: str | None = None,
) -> RequestBlock:
    return RequestBlock(
        method=method.upper(),
        path_rendered=path_rendered,
        path_rendered_sha256=sha256_text(path_rendered),
        headers=redact_headers(headers or {}),
        body=body,
        body_sha256=sha256_text(body) if body is not None else None,
    )


def build_response(
    *,
    status: int,
    body: str = "",
    content_type: str = "application/json",
    excerpt_b64: str | None = None,
) -> ResponseBlock:
    return ResponseBlock(
        status=status,
        length=len(body.encode("utf-8")),
        content_type=content_type,
        body_sha256=sha256_text(body),
        body_excerpt_b64=excerpt_b64,
        excerpt_truncated=False,
    )


def build_step(
    *,
    n: int,
    role: str,
    route_ref: str,
    request: RequestBlock,
    response: ResponseBlock,
    account: str = "",
    peer_ip: str | None = None,
    elapsed_ms: int = 0,
    note: str | None = None,
) -> Step:
    return Step(
        n=n,
        role=role,
        account=account,
        route_ref=route_ref,
        request=request,
        response=response,
        peer_ip=peer_ip,
        peer_ip_sha256=sha256_text(peer_ip) if peer_ip else "",
        elapsed_ms=elapsed_ms,
        note=note,
    )


def route_block(
    *,
    routeset: RouteSet,
    ref: str,
    resolved_at: str,
    discovery_evidence_ids: Iterable[str] = (),
) -> RouteBlock:
    """从**已解析**的绑定生成 route 块；解析不到即 ``CapsuleIncomplete``（不猜）。"""
    binding = find_binding(routeset, ref)
    if binding is None:
        raise CapsuleIncomplete(f"RouteSet 里没有 {ref}：证据不得凭空生成 route 块")
    return RouteBlock(
        routeset_sha256=routeset.routeset_sha256,
        ref=binding.ref,
        source=binding.source,
        sources_all=list(binding.sources_all),
        confidence=binding.confidence,
        template_sha256=binding.template_sha256,
        resolver_version=routeset.resolver_version,
        resolved_at=resolved_at,
        ttl_s=binding.ttl_s,
        discovery_evidence_ids=list(discovery_evidence_ids),
    )


# ── seal / verify ───────────────────────────────────────────────────
def seal_capsule_doc(cap: CapsuleDoc, *, prev_sha256: str | None = None) -> CapsuleDoc:
    """内容寻址 seal。1.1 缺 route 块直接拒绝（schema ``required``，27 N5）。"""
    if cap.capsule_version != LEGACY_VERSION and cap.route is None:
        raise CapsuleIncomplete("capsule 1.1 必含 route 块（27 N5 / make verify-capsule）")
    if prev_sha256 is not None:
        cap = cap.model_copy(update={"chain": cap.chain.model_copy(update={"prev_sha256": prev_sha256})})
    digest = sha256_hex(canonical_json(cap.hash_payload()))
    return cap.model_copy(update={"chain": cap.chain.model_copy(update={"self_sha256": digest})})


def verify_self_sha(cap: CapsuleDoc) -> bool:
    if not cap.chain.self_sha256:
        return False
    return sha256_hex(canonical_json(cap.hash_payload())) == cap.chain.self_sha256


def assert_self_sha(cap: CapsuleDoc) -> None:
    if not verify_self_sha(cap):
        raise CapsuleTampered(
            f"capsule 内容与 self_sha256 不符：{cap.chain.self_sha256 or '<empty>'}"
        )


def chain_capsules(caps: Sequence[CapsuleDoc]) -> list[CapsuleDoc]:
    """按报告顺序串链：``caps[i].prev = caps[i-1].self``，首条 prev 为全零。"""
    out: list[CapsuleDoc] = []
    prev = ZERO_SHA
    for cap in caps:
        sealed = seal_capsule_doc(cap, prev_sha256=prev)
        out.append(sealed)
        prev = sealed.chain.self_sha256
    return out


@dataclass(frozen=True)
class ChainReport:
    ok: bool
    root_sha256: str
    breaks: tuple[str, ...]

    def raise_if_broken(self) -> None:
        if not self.ok:
            raise ChainBroken("；".join(self.breaks))


def order_chain(caps: Sequence[CapsuleDoc]) -> list[CapsuleDoc]:
    """按 ``prev_sha256`` 血缘排序（shell 通配给的是文件名字典序，不是链序）。

    找不到唯一前驱的（环、多根、断头）**按原顺序**返回，让 ``verify_chain`` 去报断点，
    不在排序阶段偷偷"修好"。
    """
    by_prev = {cap.chain.prev_sha256: cap for cap in caps}
    heads = [c for c in caps if c.chain.prev_sha256 not in {x.chain.self_sha256 for x in caps}]
    if len(heads) != 1:
        return list(caps)
    ordered: list[CapsuleDoc] = []
    current: CapsuleDoc | None = heads[0]
    seen: set[str] = set()
    while current is not None and current.chain.self_sha256 not in seen:
        ordered.append(current)
        seen.add(current.chain.self_sha256)
        current = by_prev.get(current.chain.self_sha256)
    return ordered if len(ordered) == len(caps) else list(caps)


def verify_chain(caps: Sequence[CapsuleDoc], *, root_sha256: str | None = None) -> ChainReport:
    """顺序校验；**定位到下标**，不只给一个布尔。"""
    breaks: list[str] = []
    expected_prev = root_sha256 if root_sha256 is not None else (caps[0].chain.prev_sha256 if caps else None)
    for i, cap in enumerate(caps):
        if cap.capsule_version != LEGACY_VERSION and cap.route is None:
            breaks.append(f"[{i}] 缺 route 块（schema 1.1 必含）")
        if not verify_self_sha(cap):
            breaks.append(f"[{i}] self_sha256 不符（内容被改）")
        if expected_prev is not None and cap.chain.prev_sha256 != expected_prev:
            breaks.append(
                f"[{i}] 链断：prev={cap.chain.prev_sha256 or '<null>'} 期望 {expected_prev}"
            )
        expected_prev = cap.chain.self_sha256
    root = caps[0].chain.prev_sha256 if caps else (root_sha256 or "")
    return ChainReport(ok=not breaks, root_sha256=root or "", breaks=tuple(breaks))


# ── 序列化（canonical 字节，因此"同输入两次生成字节相同"）──────────
def strip_for_export(cap: CapsuleDoc) -> CapsuleDoc:
    """``--external``：删可定位字面量，保留哈希锚点（06 §2.2 / §4）。"""
    steps = [
        step.model_copy(
            update={
                "request": step.request.model_copy(update={"path_rendered": None}),
                "peer_ip": None,
            }
        )
        for step in cap.steps
    ]
    target = cap.target.model_copy(update={"resolved_ip": None})
    env = cap.env.model_copy(update={"egress": cap.env.egress.model_copy(update={"proxy": None})})
    return cap.model_copy(update={"steps": steps, "target": target, "env": env})


def dumps_capsule(cap: CapsuleDoc, *, external: bool = False) -> bytes:
    doc = strip_for_export(cap) if external else cap
    return canonical_json(doc.model_dump(mode="json"))


def loads_capsule(raw: bytes | str) -> CapsuleDoc:
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CapsuleIncomplete(f"capsule 不是合法 JSON：{exc}") from exc
    return CapsuleDoc.model_validate(payload)


def save_capsule(cap: CapsuleDoc, directory: str | Path) -> Path:
    """落盘为 ``<self_sha256>.json``；同哈希同字节，重写不产生新证据。"""
    assert_self_sha(cap)
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{cap.chain.self_sha256}.json"
    data = dumps_capsule(cap)
    if path.exists() and path.read_bytes() != data:
        raise ChainBroken(f"同哈希不同字节：{path}（内容寻址被破坏）")
    path.write_bytes(data)
    return path


def load_capsule(path: str | Path) -> CapsuleDoc:
    return loads_capsule(Path(path).read_bytes())


# ── replay ──────────────────────────────────────────────────────────
class ReplayMode(str, Enum):
    LIVE = "live"
    DRY = "dry"
    OFFLINE = "offline"


class ReplayBlocked(CapsuleError):
    """live 模式没有 Transport：拒绝假装测过（fail-closed）。"""

    code = "VP-5005"


@dataclass(frozen=True)
class TraceResult:
    status: int
    body_sha256: str
    error: str | None = None
    persisted_ref: str | None = None
    #: 403 是"对照成立"还是"请求被挡"——两者都长 403，语义必须显式（29 零件 B）
    return_code_semantics: str = "ok"


#: 这些语义表示"请求没跑成"，不能当作对照证据（06 §5.1.2 末两行）
FAILED_SEMANTICS = ("error", "blocked", "denied_request")


@runtime_checkable
class Transport(Protocol):
    """唯一发包入口。不得接受字符串地址（27 N1）。"""

    def send(self, step: Step, binding: RouteBinding) -> TraceResult: ...


@dataclass(frozen=True)
class ReplayEnv:
    """当前环境事实。**全部由调用方注入**，本模块不读时钟、不读网络。"""

    now: int
    mode: ReplayMode = ReplayMode.LIVE
    scope_in: frozenset[str] | None = None
    deny: frozenset[str] = frozenset()
    routeset: RouteSet | None = None
    profile_ok: bool = True
    plan_allowed: bool = True


@dataclass(frozen=True)
class ReplayResult:
    state: ReplayStatus
    reason: str
    traces: tuple[TraceResult, ...] = ()
    fingerprint: Mapping[str, bool] = dataclass_field(default_factory=dict)
    stale: bool = False
    would_send: tuple[tuple[int, str, str], ...] = ()


def find_binding(routeset: RouteSet | None, ref: str) -> RouteBinding | None:
    if routeset is None:
        return None
    return next((b for b in routeset.bindings if b.ref == ref), None)


def _blocked(cap: CapsuleDoc, env: ReplayEnv) -> str | None:
    """1 授权现状（06 §5.1.1 第 1 步）。"""
    if not env.plan_allowed:
        return "plan_denied"
    if env.scope_in is not None:
        if cap.target.asset_id in env.deny:
            return "asset_in_deny"
        if cap.target.asset_id not in env.scope_in:
            return "asset_out_of_scope"
    return None


def _route_gate(cap: CapsuleDoc, env: ReplayEnv) -> ReplayResult | None:
    """2 路由现状。必须先于"判定是否复现"（06 §5.1.1）。"""
    if cap.route is None:
        # 1.0 旧证据：没有 route 块就无法重解析 → 面无法确认
        return ReplayResult(ReplayStatus.ROUTE_UNRESOLVED, "no_route_evidence")
    if env.routeset is None:
        return None
    binding = find_binding(env.routeset, cap.route.ref)
    if binding is None:
        return ReplayResult(ReplayStatus.ROUTE_UNRESOLVED, "route_ref_not_resolvable")
    if binding.template_sha256 != cap.route.template_sha256:
        return ReplayResult(ReplayStatus.ROUTE_DRIFT, "template_sha256_changed")
    for step in cap.steps:
        if find_binding(env.routeset, step.route_ref) is None:
            return ReplayResult(ReplayStatus.ROUTE_UNRESOLVED, f"step_route_unresolved:n{step.n}")
    return None


def _status_ok(trace: TraceResult, expected: Sequence[int], recorded: int) -> bool:
    """状态是否仍属"同一种结果"。

    显式落在 ``expected`` 里算成立；此外**同一状态类**（200→201）也算成立——
    这类差异归 PARTIAL，不归 FAILED（06 §5.1.2 第二行）。
    """
    if trace.error is not None or trace.return_code_semantics in FAILED_SEMANTICS:
        return False
    if trace.status in expected:
        return True
    return recorded // 100 == trace.status // 100 and recorded > 0


def _live_fingerprint(cap: CapsuleDoc, traces: Sequence[TraceResult]) -> dict[str, bool]:
    by_role = {step.role: trace for step, trace in zip(cap.steps, traces)}
    recorded = {step.role: step.response.status for step in cap.steps}
    expected = cap.assertion.expected
    out: dict[str, bool] = {}

    def ok(trace: TraceResult) -> bool:
        return trace.error is None and trace.return_code_semantics not in FAILED_SEMANTICS

    base, probe, control = by_role.get("baseline"), by_role.get("probe"), by_role.get("control")
    if base is not None and probe is not None:
        out["probe_body_eq_baseline"] = (
            ok(base) and ok(probe) and bool(probe.body_sha256) and probe.body_sha256 == base.body_sha256
        )
    if probe is not None:
        out["probe_status_ok"] = _status_ok(probe, expected.get("probe_status", []), recorded["probe"])
    if control is not None:
        out["control_status_ok"] = _status_ok(control, expected.get("control_status", []), recorded["control"])
        out["control_denied"] = out["control_status_ok"]
    if base is not None:
        out["baseline_status_ok"] = _status_ok(base, expected.get("baseline_status", []), recorded["baseline"])
    return out


#: 结论条件 = 状态类谓词（``assertion.expected`` 声明的那些）；
#: ``*_body_*`` 属细节，只决定 OK vs PARTIAL（06 §5.1.2）。
_STATUS_KEYS = {
    "probe_status": "probe_status_ok",
    "control_status": "control_status_ok",
    "baseline_status": "baseline_status_ok",
}


def _conclusion_keys(cap: CapsuleDoc) -> set[str]:
    keys = {_STATUS_KEYS[k] for k in cap.assertion.expected if k in _STATUS_KEYS}
    if "control_status" in cap.assertion.expected:
        keys.add("control_denied")
    return keys


#: 细节谓词：只决定 OK vs PARTIAL，不决定结论成立与否（06 §5.1.2 第一行）
DETAIL_KEYS = ("probe_body_eq_baseline",)


def _fingerprint_holds(cap: CapsuleDoc, live: Mapping[str, bool]) -> bool:
    """三条件（状态类）必须逐条为真。

    ``expected`` 没声明状态谓词时退回"按 fingerprint 全量比对"（fail-closed），
    但已声明的细节谓词（body 等）永不参与结论——它只区分 OK / PARTIAL。
    """
    conclusion = _conclusion_keys(cap)
    if conclusion:
        for key in sorted(conclusion):
            if live.get(key) is not True:
                return False
        return True

    required = {k: v for k, v in cap.assertion.fingerprint.items() if k not in DETAIL_KEYS}
    if not required:
        return False  # 什么都没声明：不许默认成立
    for key, want in required.items():
        if live.get(key) is not bool(want):
            return False
    return True


def _details_equal(cap: CapsuleDoc, traces: Sequence[TraceResult]) -> bool:
    for step, trace in zip(cap.steps, traces):
        if trace.error is not None:
            return False
        if trace.status != step.response.status:
            return False
        if trace.body_sha256 != step.response.body_sha256:
            return False
    return True


def _classify(cap: CapsuleDoc, traces: Sequence[TraceResult], *, stale: bool) -> ReplayResult:
    """3 重判 + 4 细节比对（顺序不可交换，06 §5.1.1）。"""
    if cap.verdict is not Verdict.VERIFIED:
        # 只有 VERIFIED 的结论才谈"复现"；其它 verdict 的重放属于新判定，不在此处
        return ReplayResult(ReplayStatus.FAILED, "verdict_not_verified", tuple(traces), {}, stale)
    live = _live_fingerprint(cap, traces)
    holds = _fingerprint_holds(cap, live)

    control = next((t for s, t in zip(cap.steps, traces) if s.role == "control"), None)
    if control is not None and (
        control.error is not None or control.return_code_semantics in FAILED_SEMANTICS
    ):
        # 对照没跑成 = 结论未经检验，不能算 PARTIAL（06 §5.1.2 末两行）
        return ReplayResult(ReplayStatus.FAILED, "control_missing", tuple(traces), live, stale)
    if not holds:
        return ReplayResult(ReplayStatus.FAILED, "assertion_not_reproduced", tuple(traces), live, stale)

    if _details_equal(cap, traces):
        return ReplayResult(ReplayStatus.OK, "reproduced", tuple(traces), live, stale)
    network_partial = any(t.error is not None for t in traces)
    reason = "network_partial" if network_partial else "body_or_status_changed"
    return ReplayResult(ReplayStatus.PARTIAL, reason, tuple(traces), live, stale)


def _stored_traces(cap: CapsuleDoc) -> tuple[TraceResult, ...]:
    return tuple(
        TraceResult(
            status=step.response.status,
            body_sha256=step.response.body_sha256,
            error=step.err,
            persisted_ref=step.response.persisted_ref,
            return_code_semantics=step.response.return_code_semantics,
        )
        for step in cap.steps
    )


def replay(
    cap: CapsuleDoc,
    env: ReplayEnv,
    transport: Transport | None = None,
) -> ReplayResult:
    """六态 replay。顺序：完整性 → 授权 → 路由 → 重发 → 重判。

    - ``LIVE``：必须给 ``transport``；重新解析绑定后重发（不复用 capsule 里的地址）。
    - ``DRY``：不发包，只跑前 3 道门并给出"将要发什么"（返回 ``NEVER`` + 原因）。
    - ``OFFLINE``：不解析路由、不发包，用 capsule 内保存的响应重跑判定纯函数。
    """
    assert_self_sha(cap)  # 0 完整性（与网络无关，最先查）
    if cap.capsule_version != LEGACY_VERSION and cap.route is None:
        raise CapsuleIncomplete("capsule 1.1 必含 route 块")
    if env.mode is ReplayMode.OFFLINE and transport is not None:
        raise ReplayBlocked("offline-fixture 不发包：transport 必须为 None")

    stale = not env.profile_ok
    blocked = _blocked(cap, env)
    if blocked is not None:
        return ReplayResult(ReplayStatus.BLOCKED, blocked, (), {}, stale)

    if env.mode is not ReplayMode.OFFLINE:
        gate = _route_gate(cap, env)
        if gate is not None:
            return ReplayResult(gate.state, gate.reason, (), {}, stale)

    if env.mode is ReplayMode.DRY:
        plan = tuple((s.n, s.role, s.route_ref) for s in cap.steps)
        return ReplayResult(ReplayStatus.NEVER, "dry_run", (), {}, stale, plan)

    if env.mode is ReplayMode.OFFLINE:
        return _classify(cap, _stored_traces(cap), stale=stale)

    # 只有真的要发包时才要求这两个——门先判、缺件后报（否则"删绑定"会被误报成配置缺失）
    if env.routeset is None:
        raise ReplayBlocked("live 模式缺少 RouteSet：地址必须当场重解析（06 §5 第 3 步）")
    sender: Transport | None = transport
    if sender is None:
        raise ReplayBlocked("live 模式缺少 Transport：不许把'没测'记成六态之一")

    traces: list[TraceResult] = []
    for step in cap.steps:
        binding = find_binding(env.routeset, step.route_ref)
        assert binding is not None
        try:
            traces.append(sender.send(step, binding))
        except Exception as exc:  # 传输层是插件边界：失败必须留痕，不许吞掉
            traces.append(TraceResult(status=0, body_sha256="", error=type(exc).__name__))
    return _classify(cap, traces, stale=stale)


# ── 最小 CLI（B4 收工用；完整 ops 由 B10/B11 的 veripen_cli 接管）────
def capsule_schema_problems(cap: CapsuleDoc) -> list[str]:
    """``make verify-capsule`` 的规则：1.1 必含 route 块且双锚点一致（27 N5）。"""
    problems: list[str] = []
    if cap.capsule_version != LEGACY_VERSION:
        if cap.route is None:
            problems.append("capsule 1.1 缺 route 块")
        elif cap.target.template_sha256 and cap.target.template_sha256 != cap.route.template_sha256:
            problems.append("target.template_sha256 与 route.template_sha256 不一致")
    if not cap.steps:
        problems.append("steps 为空：没有请求就不是证据")
    if not cap.assertion.fingerprint:
        problems.append("assertion.fingerprint 为空：判定不可复核")
    if not cap.chain.self_sha256:
        problems.append("未 seal")
    return problems


def _load_many(paths: Iterable[str]) -> list[CapsuleDoc]:
    return [load_capsule(p) for p in paths]


def main(argv: Sequence[str]) -> int:
    caps: list[str] = []
    mode = ReplayMode.LIVE
    mode_given = False
    external = False
    verify = False
    schema_only = False
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--capsule", "-c") and i + 1 < len(argv):
            caps.append(argv[i + 1])
            i += 2
        elif arg == "--verify-capsule":
            schema_only = True
            i += 1
        elif arg == "--offline-fixture":
            mode = ReplayMode.OFFLINE
            mode_given = True
            i += 1
        elif arg == "--dry-run":
            mode = ReplayMode.DRY
            mode_given = True
            i += 1
        elif arg == "--external":
            external = True
            i += 1
        elif arg == "--verify-chain":
            verify = True
            i += 1
        elif arg in ("-h", "--help"):
            print(
                "用法: python -m veripen_core.evidence FILE.json [FILE2.json …]"
                " [--verify-chain] [--verify-capsule] [--dry-run | --offline-fixture] [--external]"
            )
            return 0
        elif not arg.startswith("-"):
            caps.append(arg)  # 支持 shell 通配：out/capsules/*.json
            i += 1
        else:
            print(f"未知参数: {arg}", file=sys.stderr)
            return 2

    if not caps:
        print("至少要给一个 --capsule", file=sys.stderr)
        return 2

    docs = _load_many(caps)
    if schema_only:
        status = 0
        for doc in docs:
            problems = capsule_schema_problems(doc)
            if problems:
                status = 1
                for line in problems:
                    print(f"SCHEMA-FAIL {line}")
            else:
                print(f"{doc.chain.self_sha256[:16]} schema-ok v{doc.capsule_version}")
        return status

    if verify:
        report = verify_chain(order_chain(docs))
        print(f"chain ok={report.ok} root={report.root_sha256[:16]}")
        for line in report.breaks:
            print(f"  BREAK {line}")
        if not report.ok:
            return 1

    if not mode_given and (verify or schema_only or external):
        return 0  # 只做校验/导出：不进入 replay（live 需要 Transport 与 RouteSet）

    env = ReplayEnv(now=0, mode=mode)
    status = 0
    for doc in docs:
        try:
            result = replay(doc, env)
        except CapsuleTampered as exc:
            print(f"{doc.chain.self_sha256[:16]} TAMPERED {exc.code}")
            status = 1
            continue
        print(
            f"{doc.chain.self_sha256[:16]} {result.state.value}"
            f"{' stale' if result.stale else ''} reason={result.reason}"
        )
        for n, role, ref in result.would_send:
            print(f"  would send n={n} role={role} ref={ref[:20]}")

    if external:
        for doc in docs:
            print(f"export {doc.chain.self_sha256[:16]} bytes={len(dumps_capsule(doc, external=True))}")
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
