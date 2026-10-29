"""guard 包：scope 三处校验 + 限流 + 审批门 + 凭据脱敏。

B3 交付范围（`21 §2` B3 批次卡）：
  scope 三处 + 令牌桶 + 审批骨架
  DoD：缩 scope 后拒绝且零请求；令牌桶 100 并发≈20/s；越界记 `scope ok=0`
"""

from __future__ import annotations

from .approval import ApprovalGate, ApprovalGuard, ApprovalOutcome, ApprovalResult, GateWhen
from .credentials import (
    REDACTED,
    SENSITIVE_HEADERS,
    Credential,
    InMemoryVault,
    RedactionFilter,
    VaultUnavailable,
    header_name_hashes,
    install_redaction_filter,
    is_sensitive,
    redact_headers,
)
from .ratelimit import (
    DEFAULT_BURST,
    DEFAULT_MAX_WAIT_S,
    DEFAULT_PER_ASSET_CONCURRENCY,
    DEFAULT_QPS,
    BucketUnavailable,
    LocalTokenBucket,
    RateLimiter,
    RedisTokenBucket,
    TargetBackoff,
    ThrottleResult,
    TokenBucket,
    get_backend,
)
from .scope import (
    DEFAULT_DENY_NETS,
    AssetSpec,
    CheckPoint,
    ScopeGuard,
    ScopePolicy,
    denied_hosts_summary,
    http_client_kwargs,
    normalize_ip,
    parse_target_url,
)

__all__ = [
    "DEFAULT_BURST",
    "DEFAULT_DENY_NETS",
    "DEFAULT_MAX_WAIT_S",
    "DEFAULT_PER_ASSET_CONCURRENCY",
    "DEFAULT_QPS",
    "REDACTED",
    "SENSITIVE_HEADERS",
    "ApprovalGate",
    "ApprovalGuard",
    "ApprovalOutcome",
    "ApprovalResult",
    "AssetSpec",
    "BucketUnavailable",
    "CheckPoint",
    "Credential",
    "GateWhen",
    "InMemoryVault",
    "LocalTokenBucket",
    "RateLimiter",
    "RedisTokenBucket",
    "RedactionFilter",
    "ScopeGuard",
    "ScopePolicy",
    "TargetBackoff",
    "ThrottleResult",
    "TokenBucket",
    "VaultUnavailable",
    "denied_hosts_summary",
    "get_backend",
    "header_name_hashes",
    "http_client_kwargs",
    "install_redaction_filter",
    "is_sensitive",
    "normalize_ip",
    "parse_target_url",
    "redact_headers",
]
