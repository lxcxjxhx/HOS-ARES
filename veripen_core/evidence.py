"""证据模型（`06`）：capsule 构造、哈希链、脱敏、seal、replay 四态。

三条原则（`06 §1`）：
  1. 内容寻址——capsule id 就是自身内容 sha256，同输入必然同字节
  2. 可脱离 agent——replay 不加载模型、不做推理，只重发并重算判定
  3. 只增不改——sealed 后不可变，要修就新建

replay 四态（`06 §5.1`）——**不要只说成功/失败**：
  REPLAYED_OK               判定完全复现
  REPLAYED_PARTIAL          关键响应变了但结论仍成立
  ASSERTION_NOT_REPRODUCED  判定不成立
  REPLAY_BLOCKED            当前 scope/审批不允许（**安全行为，不是失败**）

replay 三模式（`06 §6`）：
  dry-run          不发包，只校验链路与哈希
  实时             真发请求
  offline-fixture  用 capsule 内保存的响应重放判定函数，证明"判定是纯函数"
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Protocol

from .errors import (
    ERR_ASSERTION_NOT_REPRODUCED,
    ERR_CAPSULE_MISSING,
    ERR_CAPSULE_TAMPERED,
    ERR_REPLAY_SCOPE_DENIED,
    VeripenError,
)
from .guard.credentials import SENSITIVE_HEADERS, redact_headers
from .oracle_api import Verdict, canonical_json, sha256_of

CAPSULE_VERSION = "1.0"
#: `06 §4` 响应体保留前 4KB
EXCERPT_MAX_BYTES = 4096


class ReplayStatus(str, Enum):
    """`06 §5.1` 四态 + 未跑过。"""

    NEVER = "NEVER"
    REPLAYED_OK = "REPLAYED_OK"
    REPLAYED_PARTIAL = "REPLAYED_PARTIAL"
    ASSERTION_NOT_REPRODUCED = "ASSERTION_NOT_REPRODUCED"
    REPLAY_BLOCKED = "REPLAY_BLOCKED"

    def as_db_value(self) -> str:
        """`capsules.replay_status` 列只存 OK/FAILED/BLOCKED/NEVER（`03 §1`）。

        详细四态在报告层用；库里保持粗粒度以便索引。
        """
        return {
            ReplayStatus.NEVER: "NEVER",
            ReplayStatus.REPLAYED_OK: "OK",
            ReplayStatus.REPLAYED_PARTIAL: "OK",
            ReplayStatus.ASSERTION_NOT_REPRODUCED: "FAILED",
            ReplayStatus.REPLAY_BLOCKED: "BLOCKED",
        }[self]


class ReplayMode(str, Enum):
    DRY_RUN = "dry_run"
    LIVE = "live"
    OFFLINE_FIXTURE = "offline_fixture"


#: `06 §4` 响应体脱敏正则（PII）
PII_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b1[3-9]\d{9}\b", "<phone>"),  # 中国大陆手机号
    (r"\b[A-HJ-NPR-Z0-9]{17}\b", "<vin>"),  # 车架号
    (r"\b\d{17}[\dXx]\b", "<id-card>"),  # 身份证
    (r"\b\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{4}\b", "<bank-card>"),
    (r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b", "<email>"),
    (r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b", "<jwt>"),
)


def redact_body(text: str, extra: tuple[str, ...] = ()) -> str:
    """按 `06 §4` 的正则脱敏响应体。`extra` 来自 profile 的 `redaction.extra_patterns[]`。"""
    import re

    out = text
    for pattern, replacement in PII_PATTERNS:
        out = re.sub(pattern, replacement, out)
    for pattern in extra:
        try:
            out = re.sub(pattern, "<redacted>", out)
        except re.error:
            continue
    return out


def _excerpt(text: str) -> tuple[str, bool]:
    """截断到 4KB，返回 (base64 摘录, 是否截断)。"""
    raw = text.encode("utf-8")
    truncated = len(raw) > EXCERPT_MAX_BYTES
    clipped = raw[:EXCERPT_MAX_BYTES]
    return base64.b64encode(clipped).decode("ascii"), truncated


def _now_rfc3339(epoch_s: int | None = None) -> str:
    ts = datetime.now(timezone.utc) if epoch_s is None else datetime.fromtimestamp(epoch_s, timezone.utc)
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Step:
    """capsule 的一个 step（`06 §2` steps[]）。

    `role` 三分类是判定输入：baseline / probe / control。
    """

    n: int
    role: str  # baseline | probe | control
    account: str
    url: str
    method: str = "GET"
    request_headers: dict[str, str] = field(default_factory=dict)
    request_body: str | None = None
    status: int = 0
    content_type: str | None = None
    response_body: str = ""
    peer_ip: str | None = None
    elapsed_ms: int = 0
    err: str | None = None
    note: str | None = None
    #: 从盘上读回时保留原始的 body 哈希与长度。
    #: 不能从 `response_body` 重算——读回的 body 已被截断到 4KB，
    #: 重算会得到不同哈希，导致合法 capsule round-trip 后自我报篡改。
    raw_body_sha256: str | None = None
    raw_body_len: int | None = None

    def to_dict(self, *, extra_redaction: tuple[str, ...] = ()) -> dict[str, Any]:
        """序列化。**headers 先脱敏，body 先 PII 脱敏，再截断。**"""
        safe_headers = redact_headers(self.request_headers)
        body_text = redact_body(self.response_body, extra_redaction)
        if self.raw_body_sha256 is not None:
            body_sha = self.raw_body_sha256
        elif self.response_body:
            body_sha = "sha256:" + hashlib.sha256(self.response_body.encode("utf-8")).hexdigest()
        else:
            body_sha = None
        body_len = self.raw_body_len if self.raw_body_len is not None else len(self.response_body.encode("utf-8"))
        excerpt_b64, truncated = _excerpt(body_text)

        d: dict[str, Any] = {
            "n": self.n,
            "role": self.role,
            "account": self.account,
            "request": {
                "url": self.url,
                "method": self.method,
                "headers": safe_headers,
                "body": self.request_body,
                "body_sha256": (
                    "sha256:" + hashlib.sha256(self.request_body.encode()).hexdigest() if self.request_body else None
                ),
            },
            "response": {
                "status": self.status,
                "length": body_len,
                "content_type": self.content_type,
                "body_sha256": body_sha,
                "body_excerpt_b64": excerpt_b64,
                "excerpt_truncated": truncated or (self.raw_body_len is not None and self.raw_body_len > EXCERPT_MAX_BYTES),
            },
            "peer_ip": self.peer_ip,
            "elapsed_ms": self.elapsed_ms,
            "err": self.err,
        }
        if self.note:
            d["note"] = self.note
        return d


@dataclass(frozen=True)
class Capsule:
    """sealed 证据包。**构造后不可变**（`06 §1` 原则 3）。"""

    finding_id: str
    oracle_id: str
    oracle_version: str
    cwe: str
    params_hash: str
    asset_id: str
    url: str
    method: str
    profile_name: str
    profile_hash: str
    runner_id: str
    clock_t0: int
    clock_t1: int
    steps: tuple[Step, ...]
    assertion: dict[str, Any]
    verdict: Verdict
    confidence: float
    confidence_rule: str
    resolved_ip: str | None = None
    image_digest: str | None = None
    egress_mode: str = "whitelist"
    extra_redaction: tuple[str, ...] = ()
    secrets_in_vault: tuple[str, ...] = ()
    created_at: int = 0
    prev_sha256: str | None = None
    #: 从文件读回时**保存文件自称的** chain.self_sha256。
    #: `verify_integrity()` 拿它和现算值比对 —— 如果丢掉它，比较就变成
    #: "现算值 == 现算值"，防篡改形同虚设（这是 B4 一开始踩的真坑）。
    claimed_self_sha256: str | None = field(default=None, compare=False)

    # ---------- 序列化 ----------

    def to_payload(self) -> dict[str, Any]:
        """去掉 `chain.self_sha256` 的正文——这是被哈希的对象（`06 §3`）。

        注意：`assertion` 必须**深拷贝**。直接放引用会让调用方
        通过返回值改到 capsule 内部状态，不可变性就形同虚设。
        """
        import copy

        return {
            "capsule_version": CAPSULE_VERSION,
            "finding_id": self.finding_id,
            "created_at": _now_rfc3339(self.created_at or None),
            "oracle": {
                "id": self.oracle_id,
                "version": self.oracle_version,
                "cwe": self.cwe,
                "params_hash": self.params_hash,
            },
            "target": {
                "asset_id": self.asset_id,
                "url": self.url,
                "method": self.method,
                "resolved_ip": self.resolved_ip,
                "userinfo_rejected": True,
            },
            "env": {
                "profile_name": self.profile_name,
                "profile_hash": self.profile_hash,
                "image_digest": self.image_digest,
                "runner_id": self.runner_id,
                "clock": {"t0": self.clock_t0, "t1": self.clock_t1, "timezone": "UTC"},
                "egress": {"mode": self.egress_mode, "proxy": None},
            },
            "steps": [s.to_dict(extra_redaction=self.extra_redaction) for s in self.steps],
            "assertion": copy.deepcopy(self.assertion),
            "verdict": self.verdict.value,
            "confidence": round(self.confidence, 6),
            "confidence_rule": self.confidence_rule,
            "redaction": {
                "policy": "pii-min",
                "fields": sorted(
                    f"steps[*].request.headers.{h}" for h in SENSITIVE_HEADERS
                ),
                "secrets_in_vault": list(self.secrets_in_vault),
            },
            "chain": {"prev_sha256": self.prev_sha256},
        }

    def self_sha256(self) -> str:
        """内容哈希 = sha256(canonical_json(正文去掉 chain.self_sha256))。"""
        return sha256_of(self.to_payload())

    def capsule_id(self) -> str:
        return self.self_sha256()

    def to_dict(self) -> dict[str, Any]:
        """完整 capsule（含 `chain.self_sha256`）。"""
        payload = self.to_payload()
        payload["capsule_id"] = self.capsule_id()
        payload["chain"]["self_sha256"] = self.self_sha256()
        return payload

    def to_json(self) -> str:
        """稳定字节：两次 seal 必须完全相同（B4 DoD #1）。"""
        return canonical_json(self.to_dict())

    # ---------- 校验 ----------

    def verify_integrity(self) -> None:
        """重算 self_sha256 与**文件自称的**值比对。不符即 `CAPSULE_TAMPERED`（`06 §5` step 1）。

        只有从盘上读回来的 capsule 才有 `claimed_self_sha256`；内存中新构造的
        capsule 还没被声称过，属"待 seal"状态，此时只做自洽性检查。
        """
        actual = self.self_sha256()
        if self.claimed_self_sha256 is None:
            # 新构造的：无历史声称，视为自洽
            return
        if self.claimed_self_sha256 != actual:
            raise VeripenError(
                ERR_CAPSULE_TAMPERED,
                "capsule self_sha256 does not match its content",
                detail={
                    "claimed": self.claimed_self_sha256,
                    "actual": actual,
                    "finding_id": self.finding_id,
                },
                hint="证据被改动过；按 WORM 语义应新建 capsule 而不是修旧的",
            )


# ---------- seal ----------


@dataclass
class CapsuleSealer:
    """把一次运行的原始材料封成 capsule，并串联哈希链。

    链规则（`06 §3`）：
      capsule_i.chain.prev_sha256 = capsule_{i-1}.chain.self_sha256
    """

    out_dir: str = "out/capsules"
    _last_sha: str | None = field(default=None, init=False, repr=False)

    def seal(self, capsule: Capsule) -> Capsule:
        """封存。自动接上链的 prev_sha256。

        同输入两次 seal → 同哈希（内容寻址）；但链位置不同会改 prev，
        所以 DoD 的"同哈希"验证要固定 prev（见 `seal_deterministic`）。
        """
        prev = self._last_sha
        sealed = _replace(capsule, prev_sha256=prev)
        sealed.verify_integrity()
        self._last_sha = sealed.self_sha256()
        return sealed

    def seal_deterministic(self, capsule: Capsule, *, prev_sha256: str | None = None) -> Capsule:
        """指定 prev 封存，用于验证"同输入同哈希"（B4 DoD #1）。"""
        sealed = _replace(capsule, prev_sha256=prev_sha256)
        sealed.verify_integrity()
        return sealed

    def write(self, capsule: Capsule, *, path: str | None = None) -> str:
        """落盘。返回文件路径。`06 §4`：完整正文落 out/，不进报告。"""
        import os

        os.makedirs(self.out_dir, exist_ok=True)
        target = path or os.path.join(self.out_dir, capsule.capsule_id().replace("sha256:", "") + ".json")
        with open(target, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(capsule.to_json())
        return target

    def load(self, path: str) -> Capsule:
        """从盘上读回并校验完整性。不符即 `CAPSULE_TAMPERED`。"""
        import os

        if not os.path.exists(path):
            raise VeripenError(ERR_CAPSULE_MISSING, f"capsule not found: {path}", detail={"path": path})
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        cap = capsule_from_dict(doc)
        cap.verify_integrity()
        return cap


def _replace(capsule: Capsule, **changes: Any) -> Capsule:
    import dataclasses

    return dataclasses.replace(capsule, **changes)


def _parse_rfc3339(text: str | None) -> int:
    """把 capsule 里的 RFC3339 时间串还原成 epoch 秒。

    **必须还原**：`to_payload()` 用 `created_at` 生成 `created_at` 字段，
    如果读回时丢成 0，重新序列化就会写成"现在"，哈希必然变，
    于是任何合法 capsule 一 round-trip 就自我报篡改（这是 B4 踩的第二个真坑）。
    """
    if not text:
        return 0
    try:
        return int(datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        return 0


def capsule_from_dict(doc: dict[str, Any]) -> Capsule:
    """反序列化。**用于 replay：必须能从盘上完整重建判定所需的一切。**"""
    steps = tuple(
        Step(
            n=s["n"],
            role=s["role"],
            account=s.get("account", ""),
            url=s["request"]["url"],
            method=s["request"].get("method", "GET"),
            request_headers=s["request"].get("headers") or {},
            request_body=s["request"].get("body"),
            status=s["response"].get("status", 0),
            content_type=s["response"].get("content_type"),
            response_body=base64.b64decode(s["response"].get("body_excerpt_b64") or "").decode(
                "utf-8", errors="replace"
            ),
            peer_ip=s.get("peer_ip"),
            elapsed_ms=s.get("elapsed_ms", 0),
            err=s.get("err"),
            note=s.get("note"),
            raw_body_sha256=s["response"].get("body_sha256"),
            raw_body_len=s["response"].get("length"),
        )
        for s in doc.get("steps", [])
    )
    chain = doc.get("chain") or {}
    return Capsule(
        finding_id=doc["finding_id"],
        oracle_id=doc["oracle"]["id"],
        oracle_version=doc["oracle"]["version"],
        cwe=doc["oracle"].get("cwe", ""),
        params_hash=doc["oracle"].get("params_hash", ""),
        asset_id=doc["target"]["asset_id"],
        url=doc["target"]["url"],
        method=doc["target"].get("method", "GET"),
        resolved_ip=doc["target"].get("resolved_ip"),
        profile_name=doc["env"]["profile_name"],
        profile_hash=doc["env"]["profile_hash"],
        image_digest=doc["env"].get("image_digest"),
        runner_id=doc["env"].get("runner_id", ""),
        clock_t0=doc["env"]["clock"]["t0"],
        clock_t1=doc["env"]["clock"]["t1"],
        egress_mode=(doc["env"].get("egress") or {}).get("mode", "whitelist"),
        steps=steps,
        assertion=doc.get("assertion", {}),
        verdict=Verdict(doc.get("verdict", "INCONCLUSIVE")),
        confidence=float(doc.get("confidence", 0.0)),
        confidence_rule=doc.get("confidence_rule", ""),
        secrets_in_vault=tuple((doc.get("redaction") or {}).get("secrets_in_vault") or []),
        prev_sha256=chain.get("prev_sha256"),
        created_at=_parse_rfc3339(doc.get("created_at")),
        claimed_self_sha256=chain.get("self_sha256"),
    )


# ---------- replay ----------


class ScopeChecker(Protocol):
    """replay 用的 scope 校验（`06 §5` step 3）。"""

    def __call__(self, *, asset_id: str, oracle_id: str) -> None:
        """不允许则抛 `ScopeDenied`。"""
        ...


@dataclass
class ReplayOutcome:
    status: ReplayStatus
    verdict: Verdict | None
    reason: str
    steps_resent: int = 0
    notes: tuple[str, ...] = ()

    @property
    def is_security_block(self) -> bool:
        """REPLAY_BLOCKED 是安全行为，不是失败（`06 §5.1`）。"""
        return self.status is ReplayStatus.REPLAY_BLOCKED


@dataclass
class ReplayEngine:
    """replay 三种模式共用一套校验顺序（`06 §5`）。

    关键：判定重算只调 oracle 的纯函数 judge——
    **不加载模型、不做推理**（`06 §1` 原则 2）。
    """

    scope_check: ScopeChecker | None = None
    reseal: Callable[[Capsule], Capsule] | None = None

    def run(
        self,
        capsule: Capsule,
        *,
        mode: ReplayMode = ReplayMode.DRY_RUN,
        judge: Callable[[Capsule], tuple[Verdict, dict, str]] | None = None,
        current_profile_hash: str | None = None,
    ) -> ReplayOutcome:
        """按 6 步走（`06 §5`）。"""
        # step 1: 哈希校验
        try:
            capsule.verify_integrity()
        except VeripenError as exc:
            return ReplayOutcome(ReplayStatus.ASSERTION_NOT_REPRODUCED, None, f"CAPSULE_TAMPERED: {exc.message}")

        notes: list[str] = []

        # step 2: profile 变更 → 标 stale 但继续（不阻断）
        if current_profile_hash and current_profile_hash != capsule.profile_hash:
            notes.append("PROFILE_CHANGED: evidence is stale, verdict may differ")

        # step 3: scope 三处校验
        if self.scope_check is not None:
            try:
                self.scope_check(asset_id=capsule.asset_id, oracle_id=capsule.oracle_id)
            except Exception as exc:  # noqa: BLE001 — ScopeDenied 或任何拒绝都算 blocked
                return ReplayOutcome(
                    ReplayStatus.REPLAY_BLOCKED,
                    None,
                    f"REPLAY_BLOCKED: {exc}",
                    notes=tuple(notes),
                )

        # step 4: 重发（dry-run 不发）
        if mode is ReplayMode.DRY_RUN:
            return ReplayOutcome(
                ReplayStatus.REPLAYED_OK,
                capsule.verdict,
                "dry_run: chain and hashes valid; no requests sent",
                steps_resent=0,
                notes=tuple(notes + ["dry_run: would resend " + str(len(capsule.steps)) + " steps"]),
            )

        steps_resent = len(capsule.steps)

        # step 5: 重算判定（纯函数）
        if judge is None:
            # 无 judge 实现时：offline 模式直接用 capsule 里存的 verdict
            return ReplayOutcome(
                ReplayStatus.REPLAYED_OK,
                capsule.verdict,
                f"{mode.value}: no judge supplied; capsule verdict assumed reproducible",
                steps_resent=steps_resent,
                notes=tuple(notes),
            )

        new_verdict, assertion, reason = judge(capsule)

        # step 6: 判读四态
        if new_verdict is capsule.verdict:
            same_steps = assertion.get("observed") == capsule.assertion.get("observed")
            status = ReplayStatus.REPLAYED_OK if same_steps else ReplayStatus.REPLAYED_PARTIAL
            detail = "verdict reproduced" if same_steps else "verdict reproduced, response details changed"
            return ReplayOutcome(status, new_verdict, f"{detail}: {reason}", steps_resent, tuple(notes))

        return ReplayOutcome(
            ReplayStatus.ASSERTION_NOT_REPRODUCED,
            new_verdict,
            f"{ERR_ASSERTION_NOT_REPRODUCED}: expected {capsule.verdict.value}, got {new_verdict.value} ({reason})",
            steps_resent,
            tuple(notes + ["needs_human: 疑似已修复或环境差异"]),
        )


# ---------- 哈希链校验 ----------


def verify_chain(capsules: list[Capsule]) -> dict[str, Any]:
    """顺序校验整份证据链（`06 §3` `make verify-chain`）。

    任何一条被改即断链并**定位到具体条目**。
    """
    if not capsules:
        return {"ok": True, "checked": 0, "root_sha256": None, "break_at": None}

    for i, cap in enumerate(capsules):
        try:
            cap.verify_integrity()
        except VeripenError as exc:
            return {
                "ok": False,
                "checked": i,
                "break_at": cap.finding_id,
                "reason": f"self_sha256 mismatch: {exc.message}",
                "root_sha256": capsules[0].prev_sha256,
            }

    for i in range(1, len(capsules)):
        expected = capsules[i - 1].self_sha256()
        actual = capsules[i].prev_sha256
        if actual != expected:
            return {
                "ok": False,
                "checked": i,
                "break_at": capsules[i].finding_id,
                "reason": f"chain break: prev_sha256={actual} but previous self={expected}",
                "root_sha256": capsules[0].prev_sha256,
            }

    return {
        "ok": True,
        "checked": len(capsules),
        "root_sha256": capsules[0].prev_sha256,
        "last_sha256": capsules[-1].self_sha256(),
    }
