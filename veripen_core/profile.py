"""客户档（B16）：profile 解析 + 规范化哈希 + 启动期校验。

三条纪律（02 §5 / §6）：
1. **`profile_hash` 依赖解析后的 assets**：同一个文件换个部署环境，证据必须失效；
2. **`secret_ref` / asset 值只以符号形式存在**：源码与配置里没有地址（`27` N3）；
3. **新增客户档 = 只改 YAML**：`git diff --stat` 对本包必须为空（换行业不碰内核）。

本模块**不解析任何地址**：`value` 是一段文本，本模块只做"是否由环境注入"的存在性校验，
实际取值与解析由部署层负责。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


class ProfileError(RuntimeError):
    """profile 启动期失败：**fail-closed**（02 §6 第 8 步）。"""

    code = "VP-1005"


@dataclass(frozen=True)
class Asset:
    id: str
    kind: str
    criticality: str
    symbol: str  # 部署期注入的符号名（不是值）
    tags: tuple[str, ...] = ()

    def resolved_hash(self, value: str) -> str:
        """**解析结果参与哈希**：换环境 → hash 变 → 旧证据失效（02 §5）。"""
        return hashlib.sha256(f"{self.id}|{self.kind}|{value}".encode()).hexdigest()


@dataclass(frozen=True)
class Selector:
    id: str
    semantic_tag: str
    param_shape: Mapping[str, Any] = field(default_factory=dict)
    response_shape: Mapping[str, Any] = field(default_factory=dict)
    auth_requirement: str | None = None


@dataclass(frozen=True)
class OraclePolicy:
    enabled: tuple[str, ...] = ()
    needs_approval: tuple[str, ...] = ()


@dataclass(frozen=True)
class Profile:
    api_version: str
    name: str
    customer_id: str
    industry: str
    env: str
    engagement_id: str
    assets: tuple[Asset, ...]
    selectors: tuple[Selector, ...]
    oracles: OraclePolicy
    qps: float
    concurrency: int
    frameworks: tuple[str, ...]
    deny: tuple[str, ...] = ()
    #: 符号名 → 部署期注入的环境变量名（值不进 git）
    asset_env: Mapping[str, str] = field(default_factory=dict)

    # ── 哈希（02 §5）──────────────────────────────────────────
    def profile_hash(self, resolved: Mapping[str, str] | None = None) -> str:
        """``sha256(canonical(profile 去掉 meta.owner 与 auth.*.secret_ref) + canonical(resolved assets))``。

        ``resolved`` 缺失 = 部署还没注入 → 用符号占位，**哈希仍然稳定但对环境不敏感**，
        这种情况启动校验会直接拒（见 ``assert_resolvable``）。
        """
        payload = {
            "apiVersion": self.api_version,
            "meta": {
                "name": self.name,
                "customer_id": self.customer_id,
                "industry": self.industry,
                "env": self.env,
            },
            "scope": {"engagement_id": self.engagement_id, "deny": sorted(self.deny)},
            "selectors": [
                {
                    "id": s.id,
                    "semantic_tag": s.semantic_tag,
                    "param_shape": dict(s.param_shape),
                    "auth_requirement": s.auth_requirement,
                }
                for s in self.selectors
            ],
            "oracles": {
                "enabled": sorted(self.oracles.enabled),
                "needs_approval": sorted(self.oracles.needs_approval),
            },
            "policy": {"qps": self.qps, "concurrency": self.concurrency},
            "report": {"frameworks": sorted(self.frameworks)},
        }
        assets = sorted(
            a.resolved_hash((resolved or {}).get(a.id, a.symbol)) for a in self.assets
        )
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        blob += "|" + ",".join(assets)
        return hashlib.sha256(blob.encode()).hexdigest()

    def assert_resolvable(self, resolved: Mapping[str, str]) -> None:
        missing = [a.id for a in self.assets if not resolved.get(a.id)]
        if missing:
            raise ProfileError(f"资产未由环境注入：{missing}（fail-closed，不猜）")

    def selectors_for(self, oracle_id: str) -> tuple[Selector, ...]:
        return tuple(self.selectors)

    def allowed_oracles(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.oracles.enabled)))

    def as_contract_rows(self) -> list[dict[str, Any]]:
        """给报告/控制台看的"两档差异表"：**只列差异项，不含地址**。"""
        return [
            {"key": "industry", "value": self.industry},
            {"key": "oracles", "value": ",".join(sorted(self.oracles.enabled))},
            {"key": "selectors", "value": ",".join(sorted(s.semantic_tag for s in self.selectors))},
            {"key": "qps", "value": self.qps},
            {"key": "concurrency", "value": self.concurrency},
            {"key": "frameworks", "value": ",".join(sorted(self.frameworks))},
        ]


# ══════════════════════════════════════════════════════════════════
# 解析
# ══════════════════════════════════════════════════════════════════
def load_profile(path: str | Path) -> Profile:
    import yaml

    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if str(raw.get("apiVersion")) != "veripen.profile/v2":
        raise ProfileError(f"未知 apiVersion：{raw.get('apiVersion')}")

    meta = raw.get("meta") or {}
    scope = raw.get("scope") or {}
    routes = raw.get("routes") or {}
    oracles = raw.get("oracles") or {}
    policy = raw.get("policy") or {}
    report = raw.get("report") or {}

    assets = tuple(
        Asset(
            id=str(a["id"]),
            kind=str(a.get("kind") or "url"),
            criticality=str(a.get("criticality") or "medium"),
            symbol=str(a.get("value") or ""),
            tags=tuple(a.get("tags") or ()),
        )
        for a in (scope.get("assets") or [])
    )
    if not assets:
        raise ProfileError("profile 没有任何 assets")

    selectors = tuple(
        Selector(
            id=str(s["id"]),
            semantic_tag=str(s.get("semantic_tag") or s["id"]),
            param_shape=dict(s.get("param_shape") or {}),
            response_shape=dict(s.get("response_shape") or {}),
            auth_requirement=s.get("auth_requirement"),
        )
        for s in (routes.get("selectors") or [])
    )
    if not selectors:
        raise ProfileError("profile 没有任何 selectors（等于打不了任何面）")

    return Profile(
        api_version=str(raw["apiVersion"]),
        name=str(meta.get("name") or Path(path).parent.name),
        customer_id=str(meta.get("customer_id") or ""),
        industry=str(meta.get("industry") or "other"),
        env=str(meta.get("env") or "lab"),
        engagement_id=str(scope.get("engagement_id") or ""),
        assets=assets,
        selectors=selectors,
        oracles=OraclePolicy(
            enabled=tuple(oracles.get("enabled") or ()),
            needs_approval=tuple(oracles.get("needs_approval") or ()),
        ),
        qps=float(policy.get("qps") or 0),
        concurrency=int(policy.get("concurrency") or 1),
        frameworks=tuple(report.get("frameworks") or ()),
        deny=tuple(scope.get("deny") or ()),
        asset_env={a.id: f"VP_ASSET_{a.id.upper().replace('-', '_')}" for a in assets},
    )


# ══════════════════════════════════════════════════════════════════
# 启动校验（02 §6 第 8 步）
# ══════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class ValidationIssue:
    level: str  # fail | warn
    code: str
    detail: str


def validate_profile(
    profile: Profile,
    *,
    known_oracles: Iterable[str],
    known_tags: Iterable[str],
    resolved: Mapping[str, str] | None = None,
) -> list[ValidationIssue]:
    """启动即失败：未知 oracle / 未知标签 / QPS 超上限 / 未注入资产。"""
    issues: list[ValidationIssue] = []
    known_o, known_t = set(known_oracles), set(known_tags)

    unknown = sorted(set(profile.oracles.enabled) - known_o)
    if unknown:
        issues.append(ValidationIssue("fail", "unknown_oracle", f"未注册的 oracle：{unknown}"))

    bad_tags = sorted({s.semantic_tag for s in profile.selectors} - known_t)
    if bad_tags:
        issues.append(ValidationIssue("fail", "unknown_tag", f"标签不在字典里：{bad_tags}"))

    if profile.qps <= 0:
        issues.append(ValidationIssue("fail", "qps_missing", "policy.qps 必须 > 0"))
    if profile.qps > 50:
        issues.append(
            ValidationIssue("warn", "qps_high", f"QPS={profile.qps} 偏高，确认客户运维窗口允许")
        )

    if resolved is not None:
        try:
            profile.assert_resolvable(resolved)
        except ProfileError as exc:
            issues.append(ValidationIssue("fail", "asset_unresolved", str(exc)))

    if not profile.frameworks:
        issues.append(ValidationIssue("warn", "no_frameworks", "report.frameworks 为空，合规章节将缺失"))
    return issues


def diff_profiles(a: Profile, b: Profile) -> list[dict[str, str]]:
    """两档差异表（02 §4 的右列）：**只列差异，证明"只换 YAML"**。"""
    left = {row["key"]: str(row["value"]) for row in a.as_contract_rows()}
    right = {row["key"]: str(row["value"]) for row in b.as_contract_rows()}
    out: list[dict[str, str]] = []
    for key in sorted(set(left) | set(right)):
        if left.get(key) != right.get(key):
            out.append({"key": key, "demo_a": left.get(key, ""), "demo_b": right.get(key, "")})
    return out


__all__ = [
    "Asset",
    "OraclePolicy",
    "Profile",
    "ProfileError",
    "Selector",
    "ValidationIssue",
    "diff_profiles",
    "load_profile",
    "validate_profile",
]
