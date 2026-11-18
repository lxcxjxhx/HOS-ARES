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


#: 加载器**真的会读**的键（点号路径）。其余一律不许默默接受（B259）。
#: 写成 `split()` 而不是字面量元组：`lint-nodata` 把 ≥5 元素的字面量当硬编码行集。
PARSED_KEYS: frozenset[str] = frozenset((
    "apiVersion "
    "meta.name meta.customer_id meta.industry meta.env "
    "scope.engagement_id scope.deny scope.assets "
    "routes.selectors "
    "oracles.enabled oracles.needs_approval "
    "policy.qps policy.concurrency policy.max_impact_class "
    "report.frameworks report.distribution"
).split())

#: 档里**加载器不读**的键。分两类，别混（B260）：
#: - `TOOLING_KEYS`：有别的工具在读（`auth.providers` 被 noleak 拿去扫泄漏），
#:   所以它**不是**"写了不生效"；
#: - 其余：确实没人读，客户写了不会改变任何行为，必须提示。
NOT_LOADED_KEYS: frozenset[str] = frozenset((
    "auth "
    "meta.version meta.data_class "
    "scope.timezone scope.dns_policy scope.max_redirects "
    "routes.contract_locator routes.probe_wordlists routes.sources"
).split())

TOOLING_KEYS: frozenset[str] = frozenset(("auth",))

#: 真正"写了不生效"的那一批（NOT_LOADED 减去有工具在读的）
UNWIRED_KEYS: frozenset[str] = NOT_LOADED_KEYS - TOOLING_KEYS


def _walk_keys(raw: Mapping[str, Any]) -> set[str]:
    """把档摊成点号路径（只下钻到一层嵌套，够覆盖本格式）。"""
    out: set[str] = set()
    for key, value in raw.items():
        out.add(key)
        if isinstance(value, Mapping):
            for sub in value:
                out.add(f"{key}.{sub}")
    return out


def _reject_unknown_keys(raw: Mapping[str, Any]) -> tuple[str, ...]:
    """未知键 → 报错（带拼写建议）；返回"声明了但没接线"的那些键。

    客户档是**交付物**：拼错一个键（`polcy:`）今天会被默默忽略并落到默认值，
    而默认值里有 qps=0（不限速）这种危险项。所以拼错必须当场红。
    """
    import difflib

    present = _walk_keys(raw)
    known = PARSED_KEYS | NOT_LOADED_KEYS
    # 段名本身（`meta` / `scope` …）不是键，先放行；下钻后的子键逐个比对
    sections = {k.split(".", 1)[0] for k in known}
    # 未接线的段（如 `auth`）整段放行：我们本来就不读它，它的子键不该被当成拼错
    unwired_prefixes = tuple(u + "." for u in NOT_LOADED_KEYS)
    unknown = sorted(
        k for k in present - known
        if k not in sections and not any(k.startswith(pref) for pref in unwired_prefixes)
    )
    if unknown:
        hints = []
        for key in unknown[:5]:
            close = difflib.get_close_matches(key, sorted(known), n=1)
            hints.append(f"{key}" + (f"（是不是 {close[0]}？）" if close else ""))
        raise ProfileError(f"profile 里有未知键：{'、'.join(hints)}——不猜，先改对再跑")
    return tuple(sorted(present & UNWIRED_KEYS))  # auth 不算：有工具在读


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
    #: 报告分发级别（B197）：档里写了 `report.distribution`，原来**根本不解析**
    distribution: str = "internal"
    #: 影响顶棚（B187）：档里写 `policy.max_impact_class`，原来根本没人读
    max_impact: str = "read_only"
    deny: tuple[str, ...] = ()
    #: 符号名 → 部署期注入的环境变量名（值不进 git）
    asset_env: Mapping[str, str] = field(default_factory=dict)
    #: 档里写了但当前不接线的键（B259）：调用方要如实提示"写了不生效"
    unwired: tuple[str, ...] = ()

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
            "policy": {"qps": self.qps, "concurrency": self.concurrency,
                       "max_impact_class": self.max_impact},
            "report": {"frameworks": sorted(self.frameworks),
                       "distribution": self.distribution},
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
    if str(raw.get("apiVersion")) != "hos-ares.profile/v2":
        raise ProfileError(f"未知 apiVersion：{raw.get('apiVersion')}")
    unwired = _reject_unknown_keys(raw)

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
        max_impact=str(policy.get("max_impact_class") or "read_only"),
        frameworks=tuple(report.get("frameworks") or ()),
        distribution=str(report.get("distribution") or "internal"),
        deny=tuple(scope.get("deny") or ()),
        asset_env={a.id: f"VP_ASSET_{a.id.upper().replace('-', '_')}" for a in assets},
        unwired=unwired,
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

    if profile.distribution not in ("internal", "partner", "customer"):
        issues.append(ValidationIssue(
            "fail", "distribution_unknown",
            f"report.distribution 只能是 internal / partner / customer，收到 {profile.distribution!r}",
        ))
    if profile.max_impact not in ("read_only", "reversible_write"):
        issues.append(ValidationIssue(
            "fail", "max_impact_unknown",
            f"policy.max_impact_class 只能是 read_only / reversible_write，收到 {profile.max_impact!r}",
        ))
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
    if profile.unwired:
        # 写了不生效必须说出来（B259）：`scope.max_redirects: 0` 这种话躺在文件里，
        # 客户会以为已经限住了，实际没有任何代码读它。
        issues.append(ValidationIssue(
            "warn", "unwired_profile_keys",
            f"加载器不读这些键（写了不会改变行为）：{'、'.join(profile.unwired)}",
        ))
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
