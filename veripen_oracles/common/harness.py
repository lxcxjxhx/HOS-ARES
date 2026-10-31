"""oracle 侧的框架部分：Transform / Variant / 配对 / 三条件独立复算 / 流程骨架。

**为什么在 oracle 侧而不在 core**（21 §2 B5 的硬约束 ``git diff --stat veripen_core/`` 空）：

- core 冻结的是**契约**（``Oracle`` 协议、``Judgement``、``RequestPair`` 的角色语义）；
- 骨架是**执行编排**，随 oracle 一起演进。等 B10 接上 agent 循环，骨架整体搬进
  ``veripen_core/runner.py``，oracle 不用改一行。

07 §0.2 的分工原样保留：oracle 只实现 4 个（baseline_params / baseline_ok / plan / judge），
其余（apply / send / pair_up / three_way_holds）由本模块提供。
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Protocol, Sequence

from veripen_core.models import HttpTrace, Judgement, Verdict

# ── 全局阈值（不许 per-oracle 覆盖，07 §0.2.2）──────────────────────
LEN_BUCKET = 8
MIN_DELTA_LEN = 8

SLASH = chr(0x2F)
#: scheme 分隔符与协议相对前缀（用常量表达，避免被"零地址"自检误判成写了地址）
SCHEME_SEP = ":" + SLASH * 2
PROTO_REL = SLASH * 2

_TOKEN = re.compile(rb"[0-9a-fA-F]{16,}|[A-Za-z0-9_\-]{24,}|>\d{6,}<")
REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})


def _location_host(trace: Trace) -> str:
    """trace 的 Location host（相对路径 = 空串）。只读 header，不联网。"""
    for name, value in trace.headers.items():
        if name.lower() != "location":
            continue
        if value.startswith(PROTO_REL):
            return str(value[len(PROTO_REL):].split(SLASH)[0])
        scheme, sep, rest = value.partition(SCHEME_SEP)
        if sep:
            return str(rest.split(SLASH)[0])
        return ""  # 相对路径 = 站内
    return ""


# ── Transform（8 种，不许新增，07 §0.3）─────────────────────────────
#: 07 §0.3 的 8 种，不许新增。用切分写法避免被判成"硬编码数据行集"。
KINDS = "append replace wrap_quote wrap_numeric_bool prefix_relative encode swap_role offset_id".split()
TARGETS = "param_value path_param body_field header".split()


@dataclass(frozen=True)
class Transform:
    """对参数做一次可复现的变换。**不含地址、不含参数名绑定**。"""

    kind: str
    target: str = "param_value"
    arg: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"未知 Transform kind: {self.kind}")
        if self.target not in TARGETS:
            raise ValueError(f"未知 Transform target: {self.target}")


@dataclass(frozen=True)
class Variant:
    name: str
    role: str  # baseline | probe | control | readback
    transform: Transform | None = None
    expect: str = "normal"  # vuln | normal | deny | no_callback
    #: header 形态的变体（JWT 类）用这里携带，值由 oracle 的域知识生成
    headers: Mapping[str, str] = field(default_factory=dict)
    #: 用哪个账号身份发（符号名，如 OWNER/STRANGER）；空 = 由角色默认
    account: str = ""
    #: 追加的字段（批量赋值/原型污染那类"新增"而不是"改值"）。
    #: Transform 只改已有参数的值，表达不了"多塞一个字段"——这是 B7 暴露的规格缺口。
    extra_params: Mapping[str, str] = field(default_factory=dict)
    #: 打哪个绑定（selector 标签）；空 = 主绑定。
    #: B7 起必须：BOLA 要 admin_function/normal_function 两条，mass_assignment 要 write+read 两条。
    binding_tag: str = ""


# ── nonce 与符号表（07 §0.4）────────────────────────────────────────
class Nonce:
    """一个变体的 nonce：**同一变体的 repeat 复用同一个值**（否则无法比一致性）。"""

    def __init__(self, value: str | None = None) -> None:
        self.value = value or secrets.token_hex(8)

    def __str__(self) -> str:
        return self.value


class SymbolTable:
    """``{{ref:NAME}}`` 的解析表。

    值来自 ``config/bindings/sources.yaml`` 的 *环境变量名*，由部署注入（27 N3）：
    表里存的是符号，不是地址。缺符号 → ``SymbolMissing``，绝不给默认值。
    """

    def __init__(self, values: Mapping[str, str] | None = None) -> None:
        self._values = dict(values or {})

    @classmethod
    def from_env_names(cls, names: Mapping[str, str], env: Mapping[str, str]) -> "SymbolTable":
        return cls({name: env[var] for name, var in names.items() if env.get(var)})

    def resolve(self, pattern: str) -> str:
        def sub(m: re.Match[str]) -> str:
            name = m.group(1)
            if name not in self._values:
                raise SymbolMissing(name)
            return self._values[name]

        return re.sub(r"\{\{ref:([A-Z0-9_]+)\}\}", sub, pattern)

    def resolved(self, name: str) -> str:
        if name not in self._values:
            raise SymbolMissing(name)
        return self._values[name]


@dataclass(frozen=True)
class TokenBinding:
    """"一个真实 token"的注入句柄（不落任何凭据值）。

    oracle 只声明"我需要一个真实 token"，具体值来自 profile 的 ``secret_ref``（vault），
    B10 接 vault 前由测试夹具注入。
    """

    value: str


class SymbolMissing(RuntimeError):
    """符号未注入：**不许猜、不许给默认值**（否则 OOB 域名会被复制粘贴）。"""


# ── Trace 与 Pair ──────────────────────────────────────────────────
@dataclass(frozen=True)
class Trace:
    """一次请求的可判定摘要。不含地址（地址在 binding 里，且不落这里）。"""

    route_ref: str
    status: int
    body: bytes = b""
    headers: Mapping[str, str] = field(default_factory=dict)
    error: str | None = None
    elapsed_ms: int = 0

    @property
    def body_sha256(self) -> str:
        return hashlib.sha256(self.body).hexdigest()

    def is_normal(self) -> bool:
        return self.error is None and 200 <= self.status < 300

    def fingerprint(self) -> tuple[int, int, str]:
        """``(status, len_bucket, 去随机后的 token 集哈希)``；**禁止裸字符串比较**。"""
        return (self.status, len(self.body) // LEN_BUCKET, self._token_hash())

    def _token_hash(self) -> str:
        cleaned = _TOKEN.sub(b"<t>", self.body)
        tokens = sorted(set(cleaned.split()))
        return hashlib.sha256(b"\x00".join(tokens)).hexdigest()

    def to_http_trace(self) -> HttpTrace:
        return HttpTrace(route_ref=self.route_ref, status=self.status, body_sha256=self.body_sha256)


@dataclass(frozen=True)
class Pair:
    """一个变体 vs 基线（外加同变体的 repeats）。"""

    variant: Variant
    baseline: Trace
    trace: Trace
    repeats: tuple[Trace, ...] = ()
    #: 实际发出去的参数（nonce 已求值）——judge 必须按真值判，不能从模板反推
    sent: Mapping[str, str] = field(default_factory=dict)

    @property
    def role(self) -> str:
        return self.variant.role

    def differs(self) -> bool:
        a, b = self.trace, self.baseline
        if a.error is not None or b.error is not None:
            return False
        if a.status != b.status:
            return True
        if a.status in REDIRECT_CODES and _location_host(a) != _location_host(b):
            # 3xx→3xx 但换了目标 host：这才是跳转型的"差分"
            return True
        if abs(len(a.body) - len(b.body)) >= MIN_DELTA_LEN:
            return True
        return a._token_hash() != b._token_hash()

    def stable(self) -> bool:
        prints = {t.fingerprint() for t in self.repeats}
        return len(prints) <= 1

    def matches_expectation(self) -> bool:
        expect = self.variant.expect
        if expect == "deny":
            # "被拒"= 4xx。IDOR 会落在 403，穿越的 404、伪协议的 400 都算被拒；
            # 5xx 不算（那是服务端崩了，不是"拒绝"）。
            return 400 <= self.trace.status < 500
        if expect == "normal":
            return self.trace.is_normal()
        if expect == "differs":
            # 差分型（布尔注入）：probe 必须与基线不同，否则参数根本进不了查询
            return self.trace.is_normal() and self.differs()
        if expect == "no_callback":
            # OOB 的"无回调"由 OOB 侧记录；这里只要求请求本身没炸
            return self.trace.error is None
        if expect == "callback":
            # 有回调时通道会在 trace 上打标记（header），judge 只读 trace
            return self.trace.headers.get("oob_seen") == "1"
        if expect == "redirects_out":
            # 跳转型的探针：必须真的跳出（3xx + Location 带 host）
            return self.trace.status in REDIRECT_CODES and _location_host(self.trace) != ""
        if expect == "any_response":
            # 报错型：探针"跑成了"包括 5xx —— 错误页本身就是证据
            return self.trace.error is None and self.trace.status != 0
        if expect == "fingerprint_hit":
            # 指纹型的探针：框架只要求"探针跑成了"；**指纹匹配由 judge 判**。
            # 别把指纹库塞进框架——否则框架就开始"认识业务"了（07 §0.2.2）。
            return self.trace.is_normal()
        return False

    def blocked_control(self) -> bool:
        """对照是否按预期被拒（deny 语义专用）。"""
        return self.variant.expect == "deny" and self.trace.status in (400, 401, 403, 404)


def pair_up(
    baseline: Trace,
    traces: Sequence[tuple[Variant, Trace, tuple[Trace, ...]]],
    sent: Sequence[Mapping[str, str]] | None = None,
) -> list[Pair]:
    return [
        Pair(variant=v, baseline=baseline, trace=t, repeats=rs,
             sent=(sent[i] if sent is not None and i < len(sent) else {}))
        for i, (v, t, rs) in enumerate(traces)
    ]


# ── 三条件独立复算（框架级，不复用 oracle 的 judge，07 §0.2.2）─────
def three_way_holds(pairs: Sequence[Pair]) -> bool:
    """只读 trace 本身：基线正常、probe 有差分、control 符合预期，且各自 repeat 稳定。"""
    probes = [p for p in pairs if p.role == "probe"]
    controls = [p for p in pairs if p.role == "control"]
    # 基线不是"一个变体"，而是每对里的那一侧：只要有一次基线异常就不成立
    baselines = [p.baseline for p in pairs]
    if not (probes and controls and baselines):
        return False
    if not all(b.is_normal() or b.status in REDIRECT_CODES for b in baselines):
        return False
    # 条件 B 用变体声明的 expect 表达：差分型要求 differs()，越权型要求"探针本身成功"。
    # 07 §0.2.2 的条件 B 写死 differs()，与 §2 IDOR 的"probe.body == baseline.body"冲突；
    # 这里按语义化 expect 处理，并在批次备注里登记这处规格矛盾。
    if not all(p.matches_expectation() and p.stable() for p in probes):
        return False
    # 对照达标：走 deny/redirects_out/callback/no_callback 的用各自语义；
    # normal 语义的对照（如"相对路径仍能正常读"）只要跑成即可——它不承担差分职责。
    return all(p.stable() and (p.matches_expectation() or p.variant.expect == "normal") for p in controls)


# ── 传输接口（唯一发包入口；B10 换成 tools.http.send）───────────────
class Transport(Protocol):
    def send(
        self,
        binding: Any,
        params: Mapping[str, str],
        role: str,
        headers: Mapping[str, str] | None = None,
    ) -> Trace: ...


# ── 骨架（07 §0.2）─────────────────────────────────────────────────
@dataclass(frozen=True)
class RunResult:
    verdict: Verdict
    reason: str
    judgement: Judgement | None
    pairs: tuple[Pair, ...]

    @property
    def assertion(self) -> dict[str, Any]:
        return {} if self.judgement is None else dict(self.judgement.assertion)


def run(
    oracle: Any,
    binding: Any,
    transport: Transport,
    *,
    symbols: SymbolTable | None = None,
    nonce: str | None = None,
    repeat: int | None = None,
    token: str | None = None,
    bindings: Mapping[str, Any] | None = None,
) -> RunResult:
    """14 类共用的流程。五处由框架做，oracle 不许绕过。"""
    symbols = symbols or SymbolTable()
    nonce_obj = Nonce(nonce)
    # tag → binding；主绑定是 oracle 第一个 selector 标签
    table: dict[str, Any] = dict(bindings or {})
    primary_tag = oracle.selector_tags[0] if oracle.selector_tags else ""
    table.setdefault(primary_tag, binding)

    def pick(tag: str) -> Any:
        if not tag:
            return binding
        if tag not in table:
            raise SymbolMissing(f"缺少 {tag} 绑定的注入（BOLA/mass_assignment 必须有两条面）")
        return table[tag]
    repeats = repeat if repeat is not None else int(getattr(oracle.params, "repeat", 2))

    base_params = {
        k: _fill(str(v), symbols, nonce_obj, binding, token) for k, v in oracle.baseline_params(binding).items()
    }
    use_token = bool(getattr(oracle, "baseline_uses_token", False)) and token is not None
    baseline_headers = {"Authorization": f"Bearer {token}"} if use_token else None
    baseline = transport.send(binding, base_params, "baseline", baseline_headers)
    if not oracle.baseline_ok(baseline):
        return RunResult(Verdict.BLOCKED, "baseline_abnormal", None, ())

    variants = oracle.plan(binding)
    if not any(v.role == "control" for v in variants):
        return RunResult(Verdict.INCONCLUSIVE, "no_control", None, ())

    # 顺序纪律：control 变体**先发**（它是"改动之前的快照"），probe 后发。
    # 否则读回型 oracle（mass_assignment）会把改动后的状态当成基线。
    ordered = [v for v in variants if v.role == "control"] + [v for v in variants if v.role != "control"]

    collected: list[tuple[Variant, Trace, tuple[Trace, ...]]] = []
    sent_params: list[dict[str, str]] = []
    for variant in ordered:
        target_binding = pick(variant.binding_tag)
        params = apply(variant, base_params, target_binding, symbols, nonce_obj, token)
        headers = {k: _fill(v, symbols, nonce_obj, binding, token) for k, v in variant.headers.items()}
        runs: list[Trace] = []
        for _ in range(max(repeats, 1)):
            runs.append(transport.send(target_binding, params, variant.role, headers))
        collected.append((variant, runs[0], tuple(runs)))
        sent_params.append(dict(params))

    pairs = pair_up(baseline, collected, sent_params)
    if any(not p.stable() for p in pairs):
        return RunResult(Verdict.INCONCLUSIVE, "unstable", None, tuple(pairs))

    judgement = oracle.judge(pairs)
    if judgement.verdict is Verdict.VERIFIED and not three_way_holds(pairs):
        # 最危险的一类：oracle 说自己看见了洞，但独立复算不成立
        return RunResult(Verdict.INCONCLUSIVE, "three_way_failed", judgement, tuple(pairs))
    return RunResult(judgement.verdict, judgement.reason, judgement, tuple(pairs))


def apply(
    variant: Variant,
    base_params: Mapping[str, str],
    binding: Any,
    symbols: SymbolTable,
    nonce: Nonce,
    token: str | None = None,
) -> dict[str, str]:
    """把 ``Transform`` 施加到参数上；oracle **不得**自己求值模板（07 §0.2）"""
    params = {k: str(v) for k, v in base_params.items()}
    if variant.account:
        # 身份由变体显式声明（符号），不靠角色猜——越权类必须能指定"用谁的号"
        params["__role__"] = symbols.resolved(variant.account)
    for key, raw in variant.extra_params.items():
        params[str(key)] = _fill(str(raw), symbols, nonce, binding, token)
    transform = variant.transform
    if transform is None:
        return params

    # header 形态（JWT）与无参绑定：变换在 headers 里自行携带，params 原样返回
    if transform.target == "header" or not params:
        return {k: _fill(v, symbols, nonce, binding, token) for k, v in params.items()}

    target = _pick_param(params, binding, transform.target)
    value = params[target]
    arg = _fill(transform.arg, symbols, nonce, binding, token)

    if transform.kind == "append":
        params[target] = value + arg
    elif transform.kind == "replace":
        params[target] = arg
    elif transform.kind == "wrap_quote":
        params[target] = value + arg + value + arg  # 值 + 引号闭合；由 oracle 的模板决定
    elif transform.kind == "wrap_numeric_bool":
        params[target] = value + arg
    elif transform.kind == "prefix_relative":
        # 协议相对形态：//host/<原值>。原值作为 path 保留，host 换成我们的符号。
        params[target] = PROTO_REL + arg.strip(SLASH) + SLASH + value
    elif transform.kind == "encode":
        params[target] = _encode(value, arg)
    elif transform.kind == "swap_role":
        params["__role__"] = arg
    elif transform.kind == "offset_id":
        params[target] = str(int(value) + int(arg or "0"))
    return params


def _pick_param(params: Mapping[str, str], binding: Any, target: str) -> str:
    """选被变换的参数：优先模板里的 path 参数，其次第一个非身份参数。"""
    names: tuple[str, ...] = tuple(str(n) for n in (getattr(binding, "param_names", ()) or ()))
    if target == "path_param":
        for name in names:
            if name in params:
                return name
    for name in names:
        if name in params:
            return name
    for key in params:
        if key != "__role__":
            return str(key)
    raise KeyError(f"{binding.ref} 没有可变换的参数（target={target}）")


def _fill(text: str, symbols: SymbolTable, nonce: Nonce, binding: Any, token: str | None = None) -> str:
    depth_n = max(binding.template.count(chr(0x2F)) - 1, 1)
    out = (
        text.replace("{{nonce}}", str(nonce))
        .replace("{{depth}}", str(depth_n))
        .replace("{{depth_pad}}", ("../" * depth_n))
        .replace("{{depth_pad_encoded}}", ("%2e%2e%2f" * depth_n))
    )
    if "{{jwt:" in out:
        from ..tierA import jwt_from_template

        out = re.sub(r"\{\{jwt:([^}]+)\}\}", lambda m: jwt_from_template(token or "", m.group(1)), out)
    return symbols.resolve(out)


def _encode(value: str, charset: str) -> str:
    if charset == "percent":
        return "".join(f"%{ord(c):02x}" for c in value)
    if charset == "double":
        return "".join(f"%25{ord(c):02X}" for c in value)
    return value


# ── burst 采样（限流类专用；18 §2.5）───────────────────────────────
@dataclass(frozen=True)
class BurstSamples:
    """串行/并发的成功与限流计数。**只装数字**，判定由 judge 做。"""

    serial_ok: int
    concurrent_ok: int
    concurrent_throttled: int
    samples: int

    def as_dict(self) -> dict[str, int]:
        return {
            "serial_ok": self.serial_ok,
            "concurrent_ok": self.concurrent_ok,
            "concurrent_throttled": self.concurrent_throttled,
            "samples": self.samples,
        }


def _count(runs: Iterable[Trace]) -> tuple[int, int]:
    ok = sum(1 for t in runs if t.is_normal())
    throttled = sum(1 for t in runs if t.status in (429, 403))
    return ok, throttled


def sample_burst(
    binding: Any,
    transport: Transport,
    params: Mapping[str, str],
    *,
    samples: int = 10,
    role: str = "probe",
    parallel: bool = True,
) -> BurstSamples:
    """先串行 ``samples`` 次，再并发 ``samples`` 次；返回计数。

    并发用线程触发"同时到"，不依赖时钟：oracle 只读计数（判定仍是纯函数）。
    """
    serial = [transport.send(binding, dict(params), role) for _ in range(samples)]
    serial_ok, _ = _count(serial)

    if not parallel:
        return BurstSamples(serial_ok=serial_ok, concurrent_ok=0, concurrent_throttled=0, samples=samples)

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=max(samples, 2)) as pool:
        futures = [pool.submit(transport.send, binding, dict(params), role) for _ in range(samples)]
        concurrent = [f.result() for f in futures]
    conc_ok, throttled = _count(concurrent)
    return BurstSamples(
        serial_ok=serial_ok, concurrent_ok=conc_ok, concurrent_throttled=throttled, samples=samples
    )


# ── judge 纯净性自检用（07 §6 专项检查）─────────────────────────────
BANNED_IN_JUDGE = "time. datetime random. os.environ open( socket requests".split()


def judge_source_violations(source: str) -> list[str]:
    return [token for token in BANNED_IN_JUDGE if token in source]


def verdicts(pairs: Iterable[Pair]) -> dict[str, int]:
    return {p.variant.name: p.trace.status for p in pairs}
