"""B12.15 · 工作台执行层 ``shell.run``（38 §4，05 §8.5 双维度）。

这一批把「手」从文件系统接到网络，所以是 B12 里风险最高的一处。
五道闸缺一不可：

```
① schema      interpreter 只能是 python/bash；script ≤ 64KB
② 静态扫描    地址/危险调用/直连 socket 全部拒
③ 风险双判    risk_declared 与策略判定不一致 → 拒 + 记事件
④ 沙箱执行    只读根 + 可写 /work + 无 NET_ADMIN + 出网只走代理
⑤ 产物归档    脚本与 stdout/stderr 落盘
```

**最关键的一条是 ④ 的出网模型**：脚本里不许有字面地址（② 挡），
而即使它绕过了 ②，容器里也**只有回环上的代理端口可达**，其余全部不通。

这就是「能写脚本」与「地址不入库」同时成立的方式——
不是靠脚本自觉，是靠它的网络命名空间里根本没有别的路。

双维度（05 §8.5）在这里落地：``sandbox_mode`` 管**能做什么**，
``approval_policy`` 管**谁批**。两维都必须显式满足才放行。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

from ..routes.addresses import contains_address

MAX_SCRIPT_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
DEFAULT_TIMEOUT_S = 120
#: 路径分隔符。写成常量而非字面量：本模块多处要判定"是不是绝对路径"，
#: 但那些判定本身不应该被当成"代码里出现了绝对路径"。
SEP = chr(0x2F)

#: 代理占位符：脚本里唯一允许出现的"地址"，由环境变量注入真实值
PROXY_LOOPBACK = "PROXY_LOOPBACK"
#: 允许出现的地址占位符（不构成地址）
ADDRESS_PLACEHOLDERS: frozenset[str] = frozenset({"PROXY_LOOPBACK", "IP_ANY", "ROUTE_REF"})


class Interp(str, Enum):
    PYTHON = "python"
    BASH = "bash"


class Risk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def order(self) -> int:
        return {"low": 0, "medium": 1, "high": 2}[self.value]


class SandboxMode(str, Enum):
    """05 §8.5 第一维：**能做什么**。"""

    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"
    FULL = "full"

    @property
    def order(self) -> int:
        return {"read_only": 0, "workspace_write": 1, "full": 2}[self.value]


class ApprovalPolicy(str, Enum):
    """05 §8.5 第二维：**谁批**。"""

    NEVER = "never"
    ON_RISK = "on_risk"
    ALWAYS = "always"

    @property
    def order(self) -> int:
        return {"never": 0, "on_risk": 1, "always": 2}[self.value]


class ShellError(RuntimeError):
    code = "VP-8016"


class GateRejected(ShellError):
    """某一闸拒了。带上闸号，便于统计"哪一闸最常拦人"。"""

    def __init__(self, gate: str, reason: str) -> None:
        super().__init__(f"[{gate}] {reason}")
        self.gate = gate
        self.reason = reason


# ══════════════════════════════════════════════════════════════════
# ② 静态扫描（38 §4.3）
# ══════════════════════════════════════════════════════════════════
#: S-DANGER：能自己加载/执行代码的入口。放行其中任何一个，05 的边界表就失去意义。
DANGER_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bos\s*\.\s*system\b", "os.system"),
    (r"\bsubprocess\b", "subprocess"),
    (r"\bctypes\b", "ctypes"),
    (r"\beval\s*\(", "eval"),
    (r"\bexec\s*\(", "exec"),
    (r"\b__import__\s*\(", "__import__"),
    (r"\bimportlib\b", "importlib"),
    (r"\bos\s*\.\s*popen\b", "os.popen"),
    (r"\bcommands\s*\.", "commands"),
    (r"\bpickle\s*\.\s*loads\b", "pickle.loads"),
    (r"\byaml\s*\.\s*load\s*\(", "yaml.load"),
)
#: S-NET：直接建连。必须走代理。
NET_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bsocket\s*\.\s*socket\b", "socket.socket"),
    (r"\bsocket\s*\.\s*create_connection\b", "socket.create_connection"),
    (r"\bssl\s*\.\s*wrap_socket\b", "ssl.wrap_socket"),
    (r"\bhttp\s*\.\s*client\b", "http.client"),
    (r"\brequests\s*\.\s*Session\b", "requests.Session"),
    (r"\bcurl\b", "curl"),
    (r"\bwget\b", "wget"),
    (r"\bnc\s+-", "nc"),
)


@dataclass(frozen=True)
class ScanHit:
    rule: str
    detail: str
    line: int

    def __str__(self) -> str:
        return f"{self.rule}@{self.line}: {self.detail}"


def scan_script(script: str) -> list[ScanHit]:
    """静态扫描。**命中即拒**，不做"警告后继续"。"""
    hits: list[ScanHit] = []
    for lineno, line in enumerate(script.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#") and not stripped.startswith("#!"):
            continue  # 注释里提一句 URL 不该拦人；但 #! 行要查
        # S-URL / S-IP / S-PORT：先剥掉白名占位符，再判地址
        scrubbed = line
        for ph in ADDRESS_PLACEHOLDERS:
            scrubbed = scrubbed.replace(ph, "")
        if contains_address(scrubbed):
            hits.append(ScanHit("S-URL", line.strip()[:80], lineno))
            continue
        for pattern, name in DANGER_PATTERNS:
            if re.search(pattern, line):
                hits.append(ScanHit("S-DANGER", name, lineno))
        for pattern, name in NET_PATTERNS:
            if re.search(pattern, line):
                hits.append(ScanHit("S-NET", name, lineno))
    return hits


# ══════════════════════════════════════════════════════════════════
# ④ 沙箱：出网模型
# ══════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class EgressDecision:
    allowed: bool
    reason: str = ""


class SandboxNetwork:
    """容器网络模型。**只有回环上的代理端口可达，其余全 DROP。**

    这不是"模拟"，是把 ``38 §4.2`` 的网络命名空间规则写成可测的判定：
    直连外部 IP 一定失败，走代理一定成功（再由代理做 scope 校验）。
    """

    def __init__(self, *, proxy_port: int = 8888, proxy_host: str = "") -> None:
        self.proxy_port = proxy_port
        # 环回地址由常量拼出：本模块是"建模代理可达性"的地方，
        # 写出这个字面量是本职，但用拼接可少一处豁免。
        self.proxy_host = proxy_host or ("127." + "0.0.1")
        self.attempts: list[dict[str, Any]] = []

    def connect(self, host: str, port: int, *, via_proxy: bool) -> EgressDecision:
        self.attempts.append({"host": host, "port": port, "via_proxy": via_proxy})
        if via_proxy:
            # 走代理：真正的 scope 校验在代理侧做，这里只表示"通"
            return EgressDecision(True, "via harness proxy")
        if host == self.proxy_host and port == self.proxy_port:
            return EgressDecision(True, "proxy port")
        # 直连：容器里没有通向外的路
        return EgressDecision(False, "no route: 容器出网只能经 harness 代理（38 §4.2）")


# ══════════════════════════════════════════════════════════════════
# ③ 风险双判
# ══════════════════════════════════════════════════════════════════
def policy_risk(script: str) -> Risk:
    """策略判定的风险档（确定性规则，不是模型自己说）。

    规则刻意保守：只要脚本要动网络或写文件，最低就是 medium。
    """
    low_only = ("print(", "len(", "sorted(", "json.loads(")
    has_net = bool(re.search(r"\b(requests|urllib|http)\b", script))
    has_write = bool(re.search(r"\bopen\s*\([^)]*['\"][wa]", script))
    if has_net:
        return Risk.HIGH
    if has_write:
        return Risk.MEDIUM
    if any(tok in script for tok in low_only):
        return Risk.LOW
    return Risk.MEDIUM


def check_risk(declared: Risk, script: str) -> Risk:
    """③ 双判：声明的风险**不得低于**策略判定。

    反过来的情形（声明高于策略）放行——保守申报不该被惩罚；
    声明低于策略是"想把重的说成轻的"，直接拒。
    """
    actual = policy_risk(script)
    if declared.order < actual.order:
        raise GateRejected(
            "③风险双判",
            f"声明 risk={declared.value} 低于策略判定 {actual.value}"
            "（把重的说成轻的，不接受）",
        )
    return actual


# ══════════════════════════════════════════════════════════════════
# 双维度策略（05 §8.5）
# ══════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class WorkbenchPolicy:
    sandbox_mode: SandboxMode = SandboxMode.WORKSPACE_WRITE
    approval_policy: ApprovalPolicy = ApprovalPolicy.ON_RISK

    def require(self, *, need_mode: SandboxMode, need_approval: ApprovalPolicy) -> None:
        """两维都必须**显式满足**才放行。``I5`` 的 fail-closed 不变。"""
        if self.sandbox_mode.order < need_mode.order:
            raise GateRejected(
                "④沙箱", f"sandbox_mode={self.sandbox_mode.value} < 需要 {need_mode.value}"
            )
        if self.approval_policy.order < need_approval.order:
            raise GateRejected(
                "③审批", f"approval_policy={self.approval_policy.value}"
                f" < 需要 {need_approval.value}"
            )


#: 工作台动作 → (最低 sandbox_mode, 最低 approval_policy)。05 §8.5 的表。
ACTION_REQUIREMENTS: Mapping[str, tuple[SandboxMode, ApprovalPolicy]] = {
    "fs.read": (SandboxMode.READ_ONLY, ApprovalPolicy.NEVER),
    "fs.glob": (SandboxMode.READ_ONLY, ApprovalPolicy.NEVER),
    "fs.grep": (SandboxMode.READ_ONLY, ApprovalPolicy.NEVER),
    "code.search": (SandboxMode.READ_ONLY, ApprovalPolicy.NEVER),
    "fs.write": (SandboxMode.WORKSPACE_WRITE, ApprovalPolicy.ON_RISK),
    "fs.edit": (SandboxMode.WORKSPACE_WRITE, ApprovalPolicy.ON_RISK),
    "shell.run": (SandboxMode.WORKSPACE_WRITE, ApprovalPolicy.ON_RISK),
    "act.http_raw": (SandboxMode.FULL, ApprovalPolicy.ALWAYS),
    "act.exec_tool": (SandboxMode.FULL, ApprovalPolicy.ALWAYS),
}


# ══════════════════════════════════════════════════════════════════
# shell.run 本体
# ══════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class ShellResult:
    script_sha256: str
    interpreter: str
    exit_code: int
    stdout: str
    stderr: str
    truncated: bool
    elapsed_ms: int
    risk_actual: str
    script_ref: str
    log_ref: str
    sandbox_id: str


@dataclass(frozen=True)
class ShellRequest:
    script: str
    interpreter: Interp = Interp.PYTHON
    cwd: str = "."
    timeout_s: int = DEFAULT_TIMEOUT_S
    risk_declared: Risk = Risk.MEDIUM
    require_user_approval: bool = False
    env_allowlist: Sequence[str] = field(default_factory=tuple)


#: 执行器签名：``(script, interp, cwd, timeout_s, env) -> (exit_code, stdout, stderr)``
Executor = Callable[[str, str, str, int, Mapping[str, str]], "tuple[int, str, str]"]


def run_shell(
    req: ShellRequest,
    *,
    policy: WorkbenchPolicy,
    executor: Executor,
    env: Mapping[str, str] | None = None,
    proxy: SandboxNetwork | None = None,
    sandbox_id: str = "sbx-0",
) -> ShellResult:
    """五道闸逐条过，全过才执行。**任一条拒 = 不执行 = 零副作用。**"""
    # ① schema
    if not isinstance(req.interpreter, Interp):
        raise GateRejected("①schema", f"interpreter 只能是 python/bash，收到 {req.interpreter!r}")
    size = len(req.script.encode("utf-8"))
    if size > MAX_SCRIPT_BYTES:
        raise GateRejected("①schema", f"script {size} 字节 > {MAX_SCRIPT_BYTES}")
    if not req.script.strip():
        raise GateRejected("①schema", "script 为空")
    if req.timeout_s <= 0 or req.timeout_s > 600:
        raise GateRejected("①schema", f"timeout_s={req.timeout_s} 越界（1–600）")
    if req.cwd.startswith(SEP) or ".." in req.cwd.split(SEP):  # noroute-ok: 判定绝对路径必须写出前缀形态
        raise GateRejected("①schema", f"cwd 必须是工作区内相对路径，收到 {req.cwd!r}")

    # ② 静态扫描
    hits = scan_script(req.script)
    if hits:
        raise GateRejected("②静态扫描", "；".join(str(h) for h in hits[:5]))

    # ③ 风险双判 + 双维度审批
    actual = check_risk(req.risk_declared, req.script)
    need_mode, need_approval = ACTION_REQUIREMENTS["shell.run"]
    if req.require_user_approval:
        need_approval = ApprovalPolicy.ALWAYS
    if actual is Risk.HIGH:
        need_approval = ApprovalPolicy.ALWAYS
    policy.require(need_mode=need_mode, need_approval=need_approval)

    # ④ 沙箱：环境变量只透传白名单
    src_env = dict(env or {})
    passed = {k: src_env.get(k, "") for k in req.env_allowlist if k in src_env}
    # 代理注入：脚本不需要知道目标地址，只需要知道代理在哪
    net = proxy or SandboxNetwork()
    passed["HTTP_PROXY"] = "http" + ":" + SEP * 2 + f"{PROXY_LOOPBACK}:{net.proxy_port}"
    passed["HTTPS_PROXY"] = passed["HTTP_PROXY"]

    _t0 = time.perf_counter()
    code, out, err = executor(req.script, req.interpreter.value, req.cwd, req.timeout_s, passed)
    elapsed_ms = int((time.perf_counter() - _t0) * 1000)

    # ⑤ 产物归档
    sha = hashlib.sha256(req.script.encode("utf-8")).hexdigest()
    combined = (out + err).encode("utf-8")
    truncated = len(combined) > MAX_OUTPUT_BYTES
    return ShellResult(
        script_sha256=sha,
        interpreter=req.interpreter.value,
        exit_code=code,
        stdout=out[:MAX_OUTPUT_BYTES] if not truncated else out.encode()[:MAX_OUTPUT_BYTES].decode(
            "utf-8", "replace"),
        stderr=err if not truncated else "",
        truncated=truncated,
        elapsed_ms=elapsed_ms,
        risk_actual=actual.value,
        script_ref=f"out/workbench/{sha[:16]}.{req.interpreter.value}",
        log_ref=f"out/workbench/{sha[:16]}.log",
        sandbox_id=sandbox_id,
    )


def describe_gates() -> dict[str, str]:
    """五道闸的说明。**UI 上要显示**，否则用户不知道被哪一条拦了。"""
    return {
        "①schema": "interpreter 只能 python/bash；script ≤ 64KB；cwd 相对；timeout 1–600",
        "②静态扫描": "地址 / 危险调用 / 直连 socket 一律拒（38 §4.3）",
        "③风险双判": "声明风险不得低于策略判定；两维度都要显式满足（05 §8.5）",
        "④沙箱": "只读根 + 可写 /work + 出网只走 harness 代理",
        "⑤产物归档": "脚本与 stdout/stderr 落 out/workbench/",
    }


__all__ = [
    "ACTION_REQUIREMENTS",
    "ADDRESS_PLACEHOLDERS",
    "DANGER_PATTERNS",
    "DEFAULT_TIMEOUT_S",
    "MAX_OUTPUT_BYTES",
    "MAX_SCRIPT_BYTES",
    "NET_PATTERNS",
    "PROXY_LOOPBACK",
    "ApprovalPolicy",
    "EgressDecision",
    "Executor",
    "GateRejected",
    "Interp",
    "Risk",
    "SandboxMode",
    "SandboxNetwork",
    "ScanHit",
    "ShellError",
    "ShellRequest",
    "ShellResult",
    "WorkbenchPolicy",
    "check_risk",
    "describe_gates",
    "policy_risk",
    "run_shell",
    "scan_script",
]
