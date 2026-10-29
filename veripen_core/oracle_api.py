"""内核接口契约（冻结）。

本模块定义 `01 §3` 的全部签名。任何 oracle 或内核模块都只通过这里交互。
改动本文件 = 改接口 = 必须同时改 `tests/unit/test_contracts.py`。

三条不许动的决定（`00 §11`）：
  1. 判定是纯函数：`Oracle.judge(pairs)` 无 IO / 无时钟 / 无随机。
  2. 三条件合取才出 VERIFIED：probe ∧ baseline ∧ control。
  3. 客户差异只进 profile；`core/` 无客户标识。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel


class ImpactClass(str, Enum):
    """影响面分级。写类本期不启用（`00 §3`）。"""

    READ_ONLY = "read_only"
    REVERSIBLE_WRITE = "reversible_write"


class Verdict(str, Enum):
    """判定四态（`00 §9` 术语表）。"""

    VERIFIED = "VERIFIED"
    REFUTED = "REFUTED"
    INCONCLUSIVE = "INCONCLUSIVE"
    BLOCKED = "BLOCKED"


class FindingState(str, Enum):
    """发现五态（`03 §4`）。"""

    CANDIDATE = "CANDIDATE"
    VERIFIED = "VERIFIED"
    REFUTED = "REFUTED"
    UNVERIFIABLE = "UNVERIFIABLE"
    BLOCKED = "BLOCKED"


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


def canonical_json(value: Any) -> str:
    """稳定序列化：键排序、无多余空白、浮点保留 6 位。

    `06 §2` 要求 capsule 内容寻址，所以任何进哈希的结构都必须先过这里。
    """
    def _norm(v: Any) -> Any:
        if isinstance(v, float):
            return round(v, 6)
        if isinstance(v, dict):
            return {str(k): _norm(val) for k, val in sorted(v.items(), key=lambda kv: str(kv[0]))}
        if isinstance(v, (list, tuple)):
            return [_norm(item) for item in v]
        if isinstance(v, Enum):
            return v.value
        return v

    return json.dumps(_norm(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def sha256_of(value: Any) -> str:
    """对任意可序列化结构算内容哈希（前缀 `sha256:`）。"""
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class HttpTrace:
    """一次请求的完整可复现记录（`01 §3.1`）。

    只存归一化后的字段：volatile 头（Date/Set-Cookie 时间戳等）在 `headers_redacted` 里被抹平，
    否则同一请求两次跑出的 capsule 哈希不同（`06 §2` 的规范化要求）。
    """

    method: str
    url: str
    status: int
    headers_redacted: dict[str, str] = field(default_factory=dict)
    body_sha256: str = ""
    body_len: int = 0
    body_preview: str = ""
    elapsed_ms: int = 0
    redirect_to: str | None = None
    error: str | None = None

    def fingerprint(self, mode: str = "content_len+token") -> dict[str, Any]:
        """差分指纹。judge 用它在不动原文的前提下比较两次响应。"""
        fp: dict[str, Any] = {
            "status": self.status,
            "body_len": self.body_len,
            "body_sha256": self.body_sha256,
            "error": self.error,
        }
        if "token" in mode:
            fp["tokens"] = sorted(set(_tokenize(self.body_preview)))
        return fp


def _tokenize(text: str) -> list[str]:
    """极简分词：只用于差分指纹，不追求语言正确性。"""
    out: list[str] = []
    buf: list[str] = []
    for ch in text:
        if ch.isalnum():
            buf.append(ch)
        elif buf:
            out.append("".join(buf).lower())
            buf.clear()
    if buf:
        out.append("".join(buf).lower())
    return out


@dataclass(frozen=True)
class RequestPair:
    """一个变体 + 它的基线与对照（`01 §3.1`）。

    `variant` 命名约定：`bool_true` / `bool_false` 为一对差分；
    `control` 必须存在且表现"正常"，否则不得 VERIFIED。
    """

    variant: str
    baseline: HttpTrace
    probe: HttpTrace


@dataclass(frozen=True)
class Judgement:
    """judge 的返回值。`confidence` 由规则算出，模型无权给定（`01 §3.1`）。"""

    verdict: Verdict
    reason: str
    assertion: dict[str, Any]
    confidence: float


@dataclass(frozen=True)
class Asset:
    id: str
    kind: str
    value: str
    criticality: str = "medium"
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class AuthHandle:
    """认证句柄。只持有引用，取用时由 vault 解密（`02 §1` #5）。"""

    provider_id: str
    type: str
    secret_ref: str
    username: str | None = None


@dataclass(frozen=True)
class OobChannel:
    """本地回调通道（ssrf_oob 用）。`host`/`port` 由 OOB listener 启动后回填。"""

    host: str
    port: int
    token: str
    timeout_s: int = 10


@dataclass(frozen=True)
class Variant:
    """oracle.plan 产出的一个待发变体。oracle 不发请求，只描述请求。"""

    variant: str
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    body: str | None = None
    expected_differential: bool = False


@dataclass(frozen=True)
class TargetContext:
    """传给 oracle 的唯一上下文（`01 §3.3`）。"""

    asset: Asset
    base_url: str
    auth: dict[str, AuthHandle] = field(default_factory=dict)
    params: BaseModel | None = None
    clock_t0: int = 0
    oob: OobChannel | None = None


@runtime_checkable
class Oracle(Protocol):
    """确定性验证器协议（`01 §3.1`）。

    契约测试 `tests/unit/test_contracts.py::test_judge_is_pure` 会反复调用 judge
    并断言结果与调用次数、时钟、随机源无关。
    """

    id: str
    version: str
    cwe: str
    impact: ImpactClass
    requires: list[str]
    params_schema: type[BaseModel]

    def applicable(self, ctx: TargetContext) -> bool: ...

    def plan(self, ctx: TargetContext, baseline: HttpTrace) -> list[Variant]: ...

    def judge(self, pairs: list[RequestPair]) -> Judgement: ...


def three_condition_judge(
    *,
    probe_matches: bool,
    baseline_normal: bool,
    control_expected: bool,
    reason_code: str,
    assertion: dict[str, Any],
    confidence: float = 0.95,
) -> Judgement:
    """三条件合取的唯一实现（`S2`）。

    这是 FP 门槛为 0 的机械保证：任何一条不成立都出不了 VERIFIED。
    所有 oracle 的 judge 都必须走这里，不许自己写 if。
    """
    observed = {
        "probe_matches": probe_matches,
        "baseline_normal": baseline_normal,
        "control_expected": control_expected,
    }
    if not probe_matches:
        return Judgement(Verdict.REFUTED, f"{reason_code}:probe_no_match", {**assertion, "observed": observed}, 0.90)
    if not baseline_normal:
        return Judgement(
            Verdict.INCONCLUSIVE, f"{reason_code}:baseline_abnormal", {**assertion, "observed": observed}, 0.50
        )
    if not control_expected:
        return Judgement(
            Verdict.INCONCLUSIVE, f"{reason_code}:control_diverged", {**assertion, "observed": observed}, 0.50
        )
    return Judgement(Verdict.VERIFIED, f"{reason_code}:conjunction_met", {**assertion, "observed": observed}, confidence)
