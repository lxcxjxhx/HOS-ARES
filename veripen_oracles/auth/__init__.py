"""JWT 变体的实现策略（B9）。

`{{jwt:SPEC}}` 是**模板占位**，由 `_fill` 调 `jwt_from_template` 求值。
B7 的实现只认 `header|payload|keep` 三段；J1–J10 需要更细的补丁（alg / kid / jku /
exp / 去签名段 / 改签名字节），所以这里给一个**向后兼容**的求值器：

- 旧格式（两段或三段管道串）：沿用 B7 语义；
- 新格式（JSON，带 ``x-vp-case`` / ``header`` / ``payload`` / ``drop_signature`` 等键）：按补丁伪造。

**为什么放在 oracle 侧**：怎么伪造 token 是认证 oracle 的域知识（20 §2.2 / 07 §6）。
"""

from __future__ import annotations

import base64
import json
from typing import Any


def _b64d(segment: str) -> dict[str, Any]:
    pad = "=" * (-len(segment) % 4)
    try:
        return dict(json.loads(base64.urlsafe_b64decode(segment + pad)))
    except Exception:
        return {}


def _b64e(obj: dict[str, Any]) -> str:
    raw = json.dumps(obj, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _split(token: str) -> tuple[str, str, str]:
    parts = token.split(".")
    if len(parts) != 3:
        return (token, "", "")
    return parts[0], parts[1], parts[2]


def _expand(token: str, spec: str) -> str:
    """把 spec 应用到 token 上；spec 是 JSON 或 B7 的管道格式。"""
    head_seg, body_seg, sig = _split(token)
    header = _b64d(head_seg)
    payload = _b64d(body_seg)

    if spec.startswith("{"):
        try:
            plan = json.loads(spec)
        except json.JSONDecodeError:
            return token
        header = {**header, **{str(k): v for k, v in (plan.get("header") or {}).items()}}
        payload = {**payload, **{str(k): v for k, v in (plan.get("payload") or {}).items()}}
        if plan.get("drop_signature"):
            sig = ""
        if plan.get("mangle_signature"):
            # 破坏签名字节（保留长度）：这是"无效签名"的干净构造方式
            sig = (sig[:-1] + "!") if sig else "!"
        if not plan.get("keep_signature", True) and not plan.get("drop_signature"):
            sig = ""
        return f"{_b64e(header)}.{_b64e(payload)}.{sig}"

    # B7 旧格式：header|payload|keep
    parts = spec.split("|")
    if len(parts) == 3:
        head_patch = _b64d(parts[0]) if parts[0] else {}
        payload_patch = _b64d(parts[1]) if parts[1] else {}
        header = {**header, **head_patch}
        payload = {**payload, **payload_patch}
        flag = parts[2]
        if flag == "False":
            sig = ""
        elif flag == "break":
            sig = (sig[:-2] if len(sig) > 2 else sig) + ("AA" if not sig.endswith("AA") else "BB")
        return f"{_b64e(header)}.{_b64e(payload)}.{sig}"
    return token
