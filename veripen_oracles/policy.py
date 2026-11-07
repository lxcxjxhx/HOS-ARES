"""启动校验（B7）：写操作与并发类**默认关闭**，且必须真的拦住。

07 §3.6 的"三步验收"里没有这一条，但 18 §2.4/§2.5 都写了"默认 enabled:false"。
写在文档里的默认值是假的默认值——它必须是一个**启动期就抛错**的检查。

规则三条：
1. ``WINDOWS: oracle -> 需要显式启用``：写操作与并发类。
2. 启用后还要过 ``max_impact_class``：``reversible_write`` 需要顶棚放行。
3. 报告分级：``rate_limit_absent`` 属"配置缺失"，severity 上限为 low（18 §2.5）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from veripen_core.models import ImpactClass


class OracleDisabled(RuntimeError):
    """默认关闭的 oracle 被调度到了：启动期拒绝，不降级。"""

    code = "VP-4003"


class ImpactCeilingExceeded(RuntimeError):
    """影响级别超过 profile 顶棚（复用 guard 的语义，这里给 oracle 侧的错误）。"""

    code = "VP-4004"


#: 默认关闭的 oracle（写操作 / 并发）
DEFAULT_OFF = frozenset({"mass_assignment", "rate_limit_absent", "xss_stored_probe"})

#: 影响级别 → 排序
_IMPACT_RANK = {ImpactClass.READ_ONLY: 0, ImpactClass.REVERSIBLE_WRITE: 1}


@dataclass(frozen=True)
class PolicyDecision:
    allowed: tuple[str, ...]
    blocked: Mapping[str, str]

    def raise_if_blocked(self) -> None:
        if self.blocked:
            first = sorted(self.blocked.items())[0]
            raise OracleDisabled(f"{first[0]}: {first[1]}")


def check_enabled(
    oracle_ids: Iterable[str],
    *,
    enabled: Iterable[str] = (),
    impact_by_oracle: Mapping[str, ImpactClass] | None = None,
    ceiling: ImpactClass = ImpactClass.READ_ONLY,
) -> PolicyDecision:
    """启动期校验。**不通过就列出来**，由调用方决定抛还是打印（启动器必须抛）。"""
    enabled_set = set(enabled)
    impact_by_oracle = impact_by_oracle or {}
    allowed: list[str] = []
    blocked: dict[str, str] = {}

    for oid in oracle_ids:
        if oid in DEFAULT_OFF and oid not in enabled_set:
            blocked[oid] = "default_off"
            continue
        impact = impact_by_oracle.get(oid, ImpactClass.READ_ONLY)
        if _IMPACT_RANK[impact] > _IMPACT_RANK[ceiling]:
            blocked[oid] = "impact_exceeds_ceiling"
            continue
        allowed.append(oid)
    return PolicyDecision(tuple(allowed), blocked)


#: severity 规则 → 报告等级。**这是产品政策，不是测量结果。**
#:
#: oracle 只声明"这是哪一类问题"（``OracleMeta.severity_rule``），等级由这张表统一给。
#: 让每个 oracle 各自写一个数字，等于谁都能往高里写；写死在这里，改动就走 review，
#: 而它直接决定客户报告里的严重度。
#:
#: 依据：能被第三方复现的数据外泄/越权/注入给 high；需要用户交互或配置缺失给 medium/low。
SEVERITY_BY_RULE: dict[str, str] = {
    "differential_data_leak": "high",
    "cross_account_read": "high",
    "server_side_fetch": "high",
    "token_acceptance": "high",
    "token_validation_defect": "high",
    "file_disclosure": "high",
    "error_based_injection": "high",
    "functional_authz_missing": "high",
    "stored_unescaped": "medium",
    "reflected_unescaped": "medium",
    # DOM 型成立时是"真的执行了"，比反射型更硬
    "dom_sink_execution": "high",
    "readonly_field_accepted": "medium",
    "unvalidated_redirect": "medium",
    "acao_reflect": "medium",
    "introspection_or_authz": "medium",
    "config_missing_rate_limit": "low",
}


def severity_for(oracle_id: str, rule: str) -> tuple[str, str]:
    """→ (severity, basis)。

    ``basis`` 是**给人看的**：``rule`` = 有规则依据，``default`` = 表里没有这条规则、
    按 medium 兜底。报告里要能看出哪一条是兜底值——否则"medium"会被读成"评估过"。
    """
    level = SEVERITY_BY_RULE.get(rule)
    if level is None:
        return "medium", "default"
    return cap_severity(oracle_id, level), "rule"


#: 报告分级上限（18 §2.5：限流缺失是"配置缺失"，不是高危）
SEVERITY_CAP = {"rate_limit_absent": "low"}

_SEVERITY_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def cap_severity(oracle_id: str, severity: str) -> str:
    """把 severity 压到该 oracle 的上限内（不许 per-profile 抬）。"""
    cap = SEVERITY_CAP.get(oracle_id)
    if cap is None:
        return severity
    return severity if _SEVERITY_ORDER.get(severity, 0) <= _SEVERITY_ORDER[cap] else cap


__all__ = [
    "DEFAULT_OFF",
    "ImpactCeilingExceeded",
    "OracleDisabled",
    "PolicyDecision",
    "SEVERITY_BY_RULE",
    "SEVERITY_CAP",
    "cap_severity",
    "severity_for",
    "check_enabled",
]
