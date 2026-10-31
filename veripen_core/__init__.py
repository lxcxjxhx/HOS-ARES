"""VeriPen 内核包。

架构纪律（01 §1 / §7）：
- 本包不依赖验证器插件包（依赖倒置）：只暴露抽象协议，具体验证器在其外部实现。
- 不导出任何“可构造路径文本”的类型（``RouteText`` 故意不存在，测试保障）。
"""

from __future__ import annotations

from .models import (
    Capsule,
    Finding,
    FindingState,
    HttpTrace,
    ImpactClass,
    Judgement,
    Oracle,
    RequestPair,
    ReplayStatus,
    RouteCall,
    RouteRef,
    Task,
    TaskState,
    Verdict,
    canonical_json,
    idem_key,
    new_ulid,
    seal_capsule,
    sha256_hex,
)

__all__ = [
    "Capsule",
    "Finding",
    "FindingState",
    "HttpTrace",
    "ImpactClass",
    "Judgement",
    "Oracle",
    "RequestPair",
    "ReplayStatus",
    "RouteCall",
    "RouteRef",
    "Task",
    "TaskState",
    "Verdict",
    "canonical_json",
    "idem_key",
    "new_ulid",
    "seal_capsule",
    "sha256_hex",
]
