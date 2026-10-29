"""错误码目录（`24 §4`）。代码里只许引用这里的常量，不许写裸字符串。"""

from __future__ import annotations

ERR_PROFILE_SCHEMA = "VP-1001"
ERR_PROFILE_SEMANTIC = "VP-1002"
ERR_SECRET_UNRESOLVED = "VP-1003"
ERR_WINDOW_INVALID = "VP-1004"

ERR_ASSET_NOT_IN_SCOPE = "VP-2001"
ERR_DENY_HIT = "VP-2002"
ERR_REDIRECT_OUT_OF_SCOPE = "VP-2003"
ERR_IMPACT_EXCEEDED = "VP-2004"
ERR_APPROVAL_REQUIRED = "VP-2005"

ERR_LEASE_LOST = "VP-3001"
ERR_BUDGET_EXHAUSTED = "VP-3002"
ERR_THROTTLE_TIMEOUT = "VP-3003"
ERR_ASSET_CIRCUIT_OPEN = "VP-3004"

ERR_ORACLE_NOT_APPLICABLE = "VP-4001"
ERR_MISSING_PREREQUISITE = "VP-4002"
ERR_RANGE_NOT_READY = "VP-4003"
ERR_INSUFFICIENT_MATERIAL = "VP-4004"

ERR_CAPSULE_TAMPERED = "VP-5001"
ERR_CAPSULE_MISSING = "VP-5002"
ERR_REPLAY_SCOPE_DENIED = "VP-5003"
ERR_ASSERTION_NOT_REPRODUCED = "VP-5004"

ERR_REPORT_MISSING_CAPSULE = "VP-6001"
ERR_SARIF_INVALID = "VP-6002"
ERR_MANIFEST_INVALID = "VP-6003"

ERR_OFFLINE_CHECKSUM = "VP-7001"
ERR_MIGRATION_MISMATCH = "VP-7002"

ERR_MODEL_UNREACHABLE = "VP-8001"
ERR_MODEL_UNSTRUCTURED = "VP-8002"
ERR_MODEL_TIMEOUT = "VP-8003"

#: retryable 决定是否进重试路径（`24 §4` 规则）
RETRYABLE: dict[str, bool] = {
    ERR_PROFILE_SCHEMA: False,
    ERR_PROFILE_SEMANTIC: False,
    ERR_SECRET_UNRESOLVED: False,
    ERR_WINDOW_INVALID: False,
    ERR_ASSET_NOT_IN_SCOPE: False,
    ERR_DENY_HIT: False,
    ERR_REDIRECT_OUT_OF_SCOPE: False,
    ERR_IMPACT_EXCEEDED: False,
    ERR_APPROVAL_REQUIRED: False,
    ERR_LEASE_LOST: True,
    ERR_BUDGET_EXHAUSTED: False,
    ERR_THROTTLE_TIMEOUT: True,
    ERR_ASSET_CIRCUIT_OPEN: True,
    ERR_ORACLE_NOT_APPLICABLE: False,
    ERR_MISSING_PREREQUISITE: False,
    ERR_RANGE_NOT_READY: True,
    ERR_INSUFFICIENT_MATERIAL: True,
    ERR_CAPSULE_TAMPERED: False,
    ERR_CAPSULE_MISSING: False,
    ERR_REPLAY_SCOPE_DENIED: False,
    ERR_ASSERTION_NOT_REPRODUCED: False,
    ERR_REPORT_MISSING_CAPSULE: False,
    ERR_SARIF_INVALID: False,
    ERR_MANIFEST_INVALID: False,
    ERR_OFFLINE_CHECKSUM: False,
    ERR_MIGRATION_MISMATCH: False,
    ERR_MODEL_UNREACHABLE: True,
    ERR_MODEL_UNSTRUCTURED: True,
    ERR_MODEL_TIMEOUT: True,
}


class VeripenError(Exception):
    """统一错误信封（`24 §4`）。HTTP 层直接序列化它。"""

    def __init__(self, code: str, message: str, *, detail: dict | None = None, hint: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}
        self.hint = hint

    @property
    def retryable(self) -> bool:
        return RETRYABLE.get(self.code, False)

    def envelope(self) -> dict:
        body: dict = {"code": self.code, "message": self.message, "retryable": self.retryable}
        if self.detail:
            body["detail"] = self.detail
        if self.hint:
            body["hint"] = self.hint
        return {"error": body}

    def http_status(self) -> int:
        if self.code.startswith("VP-1"):
            return 400
        if self.code.startswith("VP-2"):
            return 403
        if self.code.startswith("VP-3"):
            return 409
        if self.code.startswith("VP-5") or self.code.startswith("VP-6"):
            return 422
        if self.code.startswith("VP-7"):
            return 500
        if self.code.startswith("VP-8"):
            return 503
        return 500


class ScopeDenied(VeripenError):
    """scope 校验失败。fail-closed：不重试、不降级、不"尽力而为"（`01 §4`）。"""

    def __init__(self, code: str = ERR_ASSET_NOT_IN_SCOPE, message: str = "asset not in scope", **kw):
        super().__init__(code, message, **kw)
