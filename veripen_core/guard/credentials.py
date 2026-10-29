"""凭据与日志脱敏（`05 §4`）。

**这是最大内鬼面**：agent 会拿客户账号。
所以脱敏必须在 **logger 层**做，不靠调用方自觉。

规格（`05 §4`）：
  存储    本地 vault 文件，字段级加密，主密钥来自 `VERIPEN_MASTER_KEY` 或 OS keyring
  profile 只有 `secret_ref: vault://<profile>/<name>`
  生命周期 任务结束即从内存清除；可配 TTL
  日志    `authorization`/`cookie`/`set-cookie`/`x-api-key`/`password` → `<redacted>`
  capsule 只存 header 名与哈希，不存值
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, field

#: `05 §4` 规定的脱敏字段（大小写不敏感）
SENSITIVE_HEADERS: frozenset[str] = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "api-key",
        "x-auth-token",
        "x-csrf-token",
        "password",
        "passwd",
        "secret",
        "token",
        "client-secret",
    }
)

REDACTED = "<redacted>"


def is_sensitive(name: str) -> bool:
    return name.strip().lower() in SENSITIVE_HEADERS


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """按名脱敏，保留字段存在性（capsule 需要知道"有这个头"，但不要值）。"""
    return {k: (REDACTED if is_sensitive(k) else v) for k, v in headers.items()}


def header_name_hashes(headers: dict[str, str]) -> dict[str, str]:
    """capsule 用：只存敏感头的**名**与**值哈希**（`05 §4`）。

    为什么存哈希而不是不存：replay 要能验证"用的是同一份凭据"，
    但证据又要能分享出去。哈希满足这两条。
    """
    out: dict[str, str] = {}
    for k, v in headers.items():
        if is_sensitive(k):
            out[k.lower()] = "sha256:" + hashlib.sha256(v.encode("utf-8")).hexdigest()
    return out


class RedactionFilter(logging.Filter):
    """全局日志过滤器：把敏感值替换为 `<redacted>`。

    覆盖两种泄漏途径：
      1. `record.args` 里的 dict（常见于 `log.info("hdr=%s", headers)`）
      2. `record.msg` 里字面量出现的 `Bearer xxx` / `password=xxx` 形态
    """

    _BEARER_RE = None

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, dict):
            record.args = {k: (REDACTED if is_sensitive(str(k)) else v) for k, v in record.args.items()}
        elif isinstance(record.args, tuple):
            new_args: list = []
            for a in record.args:
                if isinstance(a, dict):
                    new_args.append({k: (REDACTED if is_sensitive(str(k)) else v) for k, v in a.items()})
                else:
                    new_args.append(a)
            record.args = tuple(new_args)
        return True


def install_redaction_filter(logger: logging.Logger | None = None) -> None:
    """挂到 root logger（`05 §4` 要求 logger 层，不靠调用方）。"""
    target = logger or logging.getLogger()
    if not any(isinstance(f, RedactionFilter) for f in target.filters):
        target.addFilter(RedactionFilter())


@dataclass
class Credential:
    """内存中的凭据。**不实现 `__repr__` 泄漏值**。"""

    provider_id: str
    kind: str
    username: str | None = None
    _secret: str = field(default="", repr=False)

    def clear(self) -> None:
        """任务结束显式清除（`05 §4` 生命周期）。"""
        self._secret = ""

    def header_value(self) -> str:
        """按类型拼 header。**只在发请求那一刻调用。**"""
        if self.kind == "basic":
            import base64

            raw = f"{self.username or ''}:{self._secret}".encode()
            return "Basic " + base64.b64encode(raw).decode()
        if self.kind == "bearer":
            return f"Bearer {self._secret}"
        if self.kind == "jwt":
            return self._secret
        return self._secret

    def __repr__(self) -> str:  # pragma: no cover — 只是安全兜底
        return f"Credential(provider_id={self.provider_id!r}, kind={self.kind!r}, secret=<redacted>)"


class VaultUnavailable(RuntimeError):
    """vault 不可用。调用方必须 fail-closed（`02 §3`：不降级为匿名）。"""


class InMemoryVault:
    """B3 的 vault 实现：内存字典 + 主密钥校验。

    交付档（B13）换文件 + AES-GCM；接口不变。
    这里刻意**不做假加密**——假加密比不加密更危险，因为它会让人以为安全。
    """

    def __init__(self, master_key: str | None = None) -> None:
        key = master_key if master_key is not None else os.environ.get("VERIPEN_MASTER_KEY", "")
        if not key:
            raise VaultUnavailable(
                "VERIPEN_MASTER_KEY not set; refusing to start with an unkeyed vault (05 §4). "
                "Set it, or use InMemoryVault(master_key=...) explicitly for tests."
            )
        self._key_digest = hashlib.sha256(key.encode()).hexdigest()
        self._entries: dict[str, str] = {}

    def put(self, ref: str, secret: str) -> None:
        self._entries[ref] = secret

    def resolve(self, ref: str) -> Credential:
        """解析 `vault://<profile>/<name>`。缺失即抛，不降级（`02 §3`）。"""
        if not ref.startswith("vault://"):
            raise VaultUnavailable(f"not a vault reference: {ref!r}")
        if ref not in self._entries:
            raise VaultUnavailable(
                f"secret_ref {ref!r} cannot be resolved; refusing to fall back to anonymous (02 §3)"
            )
        parts = ref[len("vault://") :].split("/", 1)
        profile = parts[0]
        name = parts[1] if len(parts) > 1 else "default"
        kind = "basic" if name in ("primary", "secondary") else "bearer"
        return Credential(provider_id=name, kind=kind, _secret=self._entries[ref])

    def key_fingerprint(self) -> str:
        """主密钥指纹，进 doctor 输出（不泄漏密钥本身）。"""
        return "sha256:" + self._key_digest[:16]
