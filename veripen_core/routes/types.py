"""路由层类型（27 §1 / §3.1 / §3.4）。

铁律：
- ``RouteRef`` 是不透明身份（``route://<source>/<opaque16>``），不含路径字符。
- ``RouteBinding`` 的构造点**只有** ``routes/resolver.py``（N2，AST 扫描保证）。
- 本模块只定义类型，不构造绑定、不发请求、不读时钟。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, NewType

#: 不透明引用（唯一由 resolver 构造；此处只是类型别名，不提供构造器）
RouteRef = NewType("RouteRef", str)


class RouteProvenance(str, Enum):
    CONTRACT = "contract"
    OBSERVED = "observed"
    CRAWL = "crawl"
    PROBE = "probe"
    OPERATOR = "operator"


class DiscoveryReason(str, Enum):
    INIT = "INIT"
    STALE = "STALE"
    NOT_FOUND = "NOT_FOUND"
    DRIFT = "DRIFT"
    OPERATOR = "OPERATOR"


class UnresolvedReason(str, Enum):
    NO_CANDIDATE = "no_candidate"
    TRUNCATED = "truncated"
    SOURCE_ERROR = "source_error"
    CONTRACT_LOCATOR_UNRESOLVED = "contract_locator_unresolved"


@dataclass(frozen=True)
class AssetRef:
    """不透明资产句柄；值（endpoint）不在这里暴露。"""

    id: str
    kind: str = "url"
    criticality: str = "medium"


@dataclass(frozen=True)
class RouteHints:
    """四类提示，均不含路径（27 §3.1）。"""

    semantic_tag: str | None = None
    param_kind: str | None = None
    param_name_hint: str | None = None
    auth_requirement: str | None = None


@dataclass(frozen=True)
class DiscoveryBudget:
    max_requests: int = 200
    max_seconds: int = 30
    per_asset_concurrency: int = 1


@dataclass(frozen=True)
class RouteQuery:
    asset: AssetRef
    hints: RouteHints = field(default_factory=RouteHints)
    auth: str | None = None
    budget: DiscoveryBudget = field(default_factory=DiscoveryBudget)
    prior_sha256: str | None = None
    reason: DiscoveryReason = DiscoveryReason.INIT
    ttl_s: int = 900
    min_confidence: float = 0.70


@dataclass
class RouteCandidate:
    """来源适配器产出的原始候选（尚无 ref，尚无 confidence 之外的裁决）。"""

    method: str
    template: str
    param_names: tuple[str, ...] = ()
    auth_requirement: str | None = None
    body_shape_sha256: str = "nobody"
    source: RouteProvenance = RouteProvenance.CONTRACT
    verifications: int = 0
    param_ambiguity: int = 0
    response_shape: dict[str, Any] = field(default_factory=dict)

    # 由 merge 填充
    structural_key: str = ""
    template_sha256: str = ""
    sources_all: tuple[str, ...] = ()
    confidence: float = 0.0
    primary_source: str = ""


@dataclass
class RouteBinding:
    """可执行绑定：只存在于内存与 capsule，不是配置项（27 §1）。

    注意：这里没有任何“拼接地址”的方法；``template`` 是解析结果，由 resolver 填入。
    """

    ref: str
    source: str
    sources_all: tuple[str, ...]
    confidence: float
    template: str
    method: str
    param_names: tuple[str, ...]
    auth_requirement: str | None
    body_shape_sha256: str
    structural_key: str
    template_sha256: str
    ttl_s: int
    verifications: int
    usable: bool
    #: 语义标签（来自契约/观测）；discovery 分档只看它，不猜路径
    semantic_tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Unresolved:
    selector: str
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class RouteSet:
    resolver_version: str
    asset_id: str
    query_hash: str
    created_at: int
    expires_at: int
    sources_used: tuple[str, ...]
    sources_failed: tuple[str, ...]
    unresolved: tuple[Unresolved, ...]
    bindings: list[RouteBinding]
    routeset_sha256: str = ""

    def without_sha(self) -> dict[str, Any]:
        return {
            "resolver_version": self.resolver_version,
            "asset_id": self.asset_id,
            "query_hash": self.query_hash,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "sources_used": list(self.sources_used),
            "sources_failed": list(self.sources_failed),
            "unresolved": [
                {"selector": u.selector, "reason": u.reason, "detail": u.detail}
                for u in self.unresolved
            ],
            "bindings": [
                {
                    "ref": b.ref,
                    "source": b.source,
                    "sources_all": list(b.sources_all),
                    "confidence": b.confidence,
                    "template": b.template,
                    "method": b.method,
                    "param_names": list(b.param_names),
                    "auth_requirement": b.auth_requirement,
                    "body_shape_sha256": b.body_shape_sha256,
                    "structural_key": b.structural_key,
                    "template_sha256": b.template_sha256,
                    "ttl_s": b.ttl_s,
                    "verifications": b.verifications,
                    "usable": b.usable,
                }
                for b in self.bindings
            ],
        }

    def expired(self, now: int) -> bool:
        return now >= self.expires_at


class RouteError(RuntimeError):
    """路由层错误基类。"""


class RouteStale(RouteError):
    """绑定过期（TTL 到）；必须重发现，不许重放旧绑定（27 §3.9）。"""

    code = "VP-2006"
    retryable = True


class RouteUnresolved(RouteError):
    """所有来源都无结果。"""


class RouteTooWeak(RouteError):
    """置信度低于门槛。"""

    code = "VP-2006"


class UnboundRoute(RouteError):
    """请求了一个不在当前 RouteSet 里的 ref（程序 bug）。"""

    code = "VP-2007"


class ContractHostError(RouteError):
    """契约 servers host 不在 assets 内（VP-1006，启动期拒绝）。"""

    code = "VP-1006"


class QuotaExceeded(RouteError):
    """重发现超配额。"""


class ContractLocatorUnresolved(RouteError):
    """契约自举失败（成环或超过跳数或解析不出 operation）。"""
