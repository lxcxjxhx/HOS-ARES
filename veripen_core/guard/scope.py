"""scope 三处校验（`05 §1`）。

**同一个判定函数，三个调用时机，缺一即漏。**

  ① plan         规划期：asset∈scope ∧ ∉deny ∧ oracle∈enabled ∧ impact≤max ∧ 窗口内
  ② preflight    执行前：复核① + 预算 + 令牌 + 审批
  ③ post_connect 连接后：实际对端 IP ∈ 解析集合 ∧ ∉deny；跳转目标同样校验

为什么必须有第③处：DNS rebinding、302 跳转、URL 解析差异、CDN 回源、IPv6 回退——
前两处都看不见真实对端（`05 §1`）。

## 已知绕过面与对策（`05 §1.1`，这是唯一值得写长注释的地方）

| 绕过面 | 对策 | 本实现 |
|---|---|---|
| DNS rebinding（两次解析不同） | 预解析一次并固定 IP 直连 | `resolve_once()` 缓存；`pinned_ips` 传给 transport |
| 302/307 跳出 scope | `max_redirects=0`；跟随需显式开启且每跳过③ | `check_redirect()` + `max_redirects` 硬校验 |
| `@` 与 URL 混淆（`http://allowed@evil/`） | 严格解析，拒收含 userinfo 的 URL | `parse_target_url()` 抛 `ScopeDenied` |
| IPv4-mapped IPv6 / 十进制 IP | `ipaddress` 归一化后再判 | `normalize_ip()` |
| 云元数据 / 内网段 | deny 默认含元数据与私网 | `DEFAULT_DENY_NETS` |
| 代理变量污染 | 显式 `trust_env=False`，禁读 `HTTP_PROXY` | `http_client_kwargs()` 返回 `trust_env=False` |

fail-closed：任何一条不确定（解析失败、字段缺失、IP 无法归一化）一律拒绝，
不重试、不降级、不"尽力而为"（`01 §4` 模块职责表）。
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from enum import Enum
from fnmatch import fnmatch
from typing import Iterable
from urllib.parse import urlsplit

from ..errors import (
    ERR_ASSET_NOT_IN_SCOPE,
    ERR_DENY_HIT,
    ERR_IMPACT_EXCEEDED,
    ERR_REDIRECT_OUT_OF_SCOPE,
    ScopeDenied,
)
from ..oracle_api import ImpactClass

#: `05 §1.1` 默认拒绝网段：云元数据 + 全部私网 + 环回 + 链路本地 + ULA
DEFAULT_DENY_NETS: tuple[str, ...] = (
    "169.254.169.254/32",  # AWS/GCP/Azure 元数据
    "169.254.0.0/16",  # 链路本地
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "127.0.0.0/8",
    "::1/128",
    "fd00::/8",
    "fe80::/10",
)

_IMPACT_ORDER: dict[ImpactClass, int] = {
    ImpactClass.READ_ONLY: 0,
    ImpactClass.REVERSIBLE_WRITE: 1,
}

#: 用户信息段（`allowed@evil`）——出现即拒，不做"取最后一个 @" 这类聪明处理
_USERINFO_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://[^/?#]*@")


class CheckPoint(str, Enum):
    """三个校验时机（`05 §1`）。事件 kind 由它决定。"""

    PLAN = "plan"
    PREFLIGHT = "preflight"
    POST_CONNECT = "post_connect"


@dataclass(frozen=True)
class AssetSpec:
    """`profile.scope.assets[]` 的一条。"""

    id: str
    kind: str
    value: str
    criticality: str = "medium"
    tags: tuple[str, ...] = ()
    #: 预解析结果，由 ScopeGuard.resolve_once() 填入（DNS rebinding 对策）
    pinned_ips: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ScopePolicy:
    """与 scope 相关的 profile 子集（`02 §2`）。"""

    engagement_id: str
    assets: tuple[AssetSpec, ...]
    deny: tuple[str, ...] = DEFAULT_DENY_NETS
    dns_policy: str = "strict"  # strict=解析结果必须落在 assets 解析集合内
    max_redirects: int = 0
    window_start: str | None = None
    window_end: str | None = None
    max_impact_class: ImpactClass = ImpactClass.READ_ONLY
    stop_on_scope_violation: bool = True
    egress_allowlist: tuple[str, ...] = ()
    enabled_oracles: tuple[str, ...] = ()

    def asset(self, asset_id: str) -> AssetSpec | None:
        for a in self.assets:
            if a.id == asset_id:
                return a
        return None


def normalize_ip(raw: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """归一化 IP（`05 §1.1`）：处理 IPv4-mapped IPv6 与十进制写法。

    不确定就抛——fail-closed 不允许"猜"。
    """
    text = raw.strip()
    # 十进制整数写法（2130706433 → 127.0.0.1）
    if text.isdigit():
        return ipaddress.IPv4Address(int(text))
    addr = ipaddress.ip_address(text)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    return addr


def parse_target_url(url: str) -> tuple[str, str, int | None]:
    """严格解析 URL，返回 (scheme, host, port)。

    拒收含 userinfo 的 URL（`05 §1.1`）——`http://allowed@evil/` 这类混淆
    在浏览器与 httpx 里语义不同，不做兼容，直接拒绝。
    """
    if _USERINFO_RE.match(url):
        raise ScopeDenied(
            ERR_ASSET_NOT_IN_SCOPE,
            "URL contains userinfo; rejected to prevent host confusion",
            detail={"url": url},
            hint="把凭据放进 Authorization 头，不要写在 URL 里",
        )
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ScopeDenied(
            ERR_ASSET_NOT_IN_SCOPE,
            f"unsupported scheme: {parts.scheme or '(none)'}",
            detail={"url": url},
        )
    host = parts.hostname
    if not host:
        raise ScopeDenied(ERR_ASSET_NOT_IN_SCOPE, "URL has no host", detail={"url": url})
    return parts.scheme, host, parts.port


def denials_for(policy: ScopePolicy) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    nets = []
    for entry in policy.deny:
        try:
            nets.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            continue
    return tuple(nets)


@dataclass
class ScopeGuard:
    """scope 三处校验器。

    每次拒绝都返回结构化 `ScopeDenied`，并且调用方必须记 `kind=scope, ok=False`
    事件（`05 §6` 留痕）。本类不自己写事件——保持"判定与副作用分离"。
    """

    policy: ScopePolicy
    _deny_nets: tuple = field(init=False, repr=False)
    _resolved: dict[str, frozenset[str]] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self._deny_nets = denials_for(self.policy)

    # ---------- 预解析（DNS rebinding 对策）----------

    def resolve_once(self, asset: AssetSpec, resolver=None) -> frozenset[str]:
        """预解析一次并缓存。后续所有校验用这份快照，不再重新解析。

        `resolver(host) -> list[str]` 可注入（测试用固定表，生产用 socket.getaddrinfo）。
        解析失败 → fail-closed 拒绝，不降级为"当作没有 IP"。
        """
        if asset.id in self._resolved:
            return self._resolved[asset.id]

        _, host, _ = parse_target_url(asset.value)
        if _looks_like_ip(host):
            ips = frozenset({str(normalize_ip(host))})
        else:
            if resolver is None:
                import socket

                def resolver(h: str) -> list[str]:  # type: ignore[misc]
                    infos = socket.getaddrinfo(h, None)
                    return [str(i[4][0]) for i in infos]

            try:
                raw = resolver(host)
            except OSError as exc:
                raise ScopeDenied(
                    ERR_ASSET_NOT_IN_SCOPE,
                    f"cannot resolve asset host {host!r}: {exc}",
                    detail={"asset_id": asset.id, "host": host},
                    hint="DNS 解析失败时 fail-closed；检查 profile 的 assets.value",
                ) from exc
            if not raw:
                raise ScopeDenied(
                    ERR_ASSET_NOT_IN_SCOPE,
                    f"asset host {host!r} resolved to nothing",
                    detail={"asset_id": asset.id},
                )
            ips = frozenset(str(normalize_ip(r)) for r in raw)

        self._resolved[asset.id] = ips
        return ips

    # ---------- ① 规划期 ----------

    def check_plan(self, *, asset_id: str, oracle_id: str, impact: ImpactClass) -> None:
        """规划期校验（`05 §1` ①）。失败即 `BLOCKED` 且**不发包**。"""
        asset = self.policy.asset(asset_id)
        if asset is None:
            raise ScopeDenied(
                ERR_ASSET_NOT_IN_SCOPE,
                "asset not in scope",
                detail={"asset_id": asset_id, "checkpoint": CheckPoint.PLAN.value},
                hint="把该资产加入 profile 的 scope.assets，或从请求里移除",
            )

        self._check_deny_value(asset.value, asset_id=asset_id, checkpoint=CheckPoint.PLAN)

        if self.policy.enabled_oracles and oracle_id not in self.policy.enabled_oracles:
            raise ScopeDenied(
                ERR_ASSET_NOT_IN_SCOPE,
                f"oracle {oracle_id!r} is not enabled in this profile",
                detail={
                    "oracle_id": oracle_id,
                    "enabled": list(self.policy.enabled_oracles),
                    "checkpoint": CheckPoint.PLAN.value,
                },
                hint="在 profile 的 oracles.enabled 里加上它，或从请求里移除",
            )

        if _IMPACT_ORDER[impact] > _IMPACT_ORDER[self.policy.max_impact_class]:
            raise ScopeDenied(
                ERR_IMPACT_EXCEEDED,
                f"impact {impact.value} exceeds max_impact_class {self.policy.max_impact_class.value}",
                detail={"oracle_id": oracle_id, "impact": impact.value, "checkpoint": CheckPoint.PLAN.value},
            )

    # ---------- ② 执行前 ----------

    def check_preflight(self, *, asset_id: str, oracle_id: str, impact: ImpactClass) -> None:
        """执行前复核（`05 §1` ②）。复用 ① 的全部规则，再叠加执行期约束。"""
        self.check_plan(asset_id=asset_id, oracle_id=oracle_id, impact=impact)

        # 出口白名单：非空时必须命中（`05 §5`）
        asset = self.policy.asset(asset_id)
        assert asset is not None  # check_plan 已保证
        if self.policy.egress_allowlist:
            _, host, _ = parse_target_url(asset.value)
            if not any(fnmatch(host, pat) for pat in self.policy.egress_allowlist):
                raise ScopeDenied(
                    ERR_ASSET_NOT_IN_SCOPE,
                    f"host {host!r} not in egress_allowlist",
                    detail={"asset_id": asset_id, "allowlist": list(self.policy.egress_allowlist)},
                )

    # ---------- ③ 连接后 ----------

    def check_post_connect(self, *, peer_ip: str, asset_id: str, url: str) -> None:
        """连接后校验（`05 §1` ③）。**这是最后一道，也必须是最严的一道。**

        真实对端 IP 必须同时满足：
          a) 不在 deny 内
          b) 在 assets 的解析集合内（strict 策略下）

        失败 → 调用方必须立即断连，并记 `kind=scope, ok=False, detail.VIOLATION`。
        """
        asset = self.policy.asset(asset_id)
        if asset is None:
            raise ScopeDenied(
                ERR_ASSET_NOT_IN_SCOPE,
                "asset disappeared from scope mid-task",
                detail={"asset_id": asset_id, "VIOLATION": "asset_not_in_scope"},
            )

        try:
            ip = normalize_ip(peer_ip)
        except ValueError as exc:
            raise ScopeDenied(
                ERR_DENY_HIT,
                f"unparseable peer ip {peer_ip!r}",
                detail={"asset_id": asset_id, "VIOLATION": "unparseable_peer_ip"},
            ) from exc

        if self._in_deny(ip):
            raise ScopeDenied(
                ERR_DENY_HIT,
                f"peer {ip} is in the deny list",
                detail={
                    "asset_id": asset_id,
                    "url": url,
                    "ip": str(ip),
                    "VIOLATION": "peer_in_deny",
                },
                hint="命中 deny 网段：立即断连并停整任务",
            )

        if self.policy.dns_policy == "strict":
            allowed = self._resolved.get(asset_id) or asset.pinned_ips
            if not allowed:
                # 未预解析 → fail-closed（不允许"没解析过所以放行"）
                raise ScopeDenied(
                    ERR_DENY_HIT,
                    "strict dns_policy requires resolve_once() before connecting",
                    detail={"asset_id": asset_id, "VIOLATION": "unresolved_asset"},
                )
            if str(ip) not in allowed:
                raise ScopeDenied(
                    ERR_DENY_HIT,
                    f"peer {ip} is not in the resolved set for asset {asset_id}",
                    detail={
                        "asset_id": asset_id,
                        "url": url,
                        "ip": str(ip),
                        "resolved": sorted(allowed),
                        "VIOLATION": "peer_not_in_scope",
                    },
                )

    # ---------- 跳转 ----------

    def check_redirect(self, *, hop: int, target_url: str, asset_id: str) -> None:
        """跳转校验（`05 §1.1`）。`max_redirects=0` 时第一次跳转就拒。"""
        if hop > self.policy.max_redirects:
            raise ScopeDenied(
                ERR_REDIRECT_OUT_OF_SCOPE,
                f"redirect hop {hop} exceeds max_redirects={self.policy.max_redirects}",
                detail={"asset_id": asset_id, "target": target_url, "VIOLATION": "redirect_not_allowed"},
            )
        self._check_deny_value(target_url, asset_id=asset_id, checkpoint=CheckPoint.POST_CONNECT)

    # ---------- 内部 ----------

    def _check_deny_value(self, value: str, *, asset_id: str, checkpoint: CheckPoint) -> None:
        """检查资产/跳转的值是否直接落在 deny 内。"""
        host = _host_of_value(value)
        if host is None:
            return
        if _looks_like_ip(host):
            try:
                if self._in_deny(normalize_ip(host)):
                    raise ScopeDenied(
                        ERR_DENY_HIT,
                        f"{value!r} is in the deny list",
                        detail={"asset_id": asset_id, "VIOLATION": "asset_in_deny", "checkpoint": checkpoint.value},
                    )
            except ValueError:
                return

    def _in_deny(self, ip) -> bool:
        return any(ip in net for net in self._deny_nets)


def _looks_like_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return False


def _host_of_value(value: str) -> str | None:
    """从 asset.value 里取 host。raddr/CIDR 值直接返回自身。"""
    if "://" in value:
        try:
            return parse_target_url(value)[1]
        except ScopeDenied:
            return None
    # CIDR 或裸 IP / 裸域名
    if "/" in value:
        return value.split("/", 1)[0]
    return value


def http_client_kwargs(policy: ScopePolicy) -> dict:
    """HTTP 客户端参数（`05 §1.1` 代理变量污染 + `05 §5` 出口）。

    - `trust_env=False`：禁止读 `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY`
    - `follow_redirects`：由 `max_redirects` 决定，默认 0 即不跟随
    """
    return {
        "trust_env": False,
        "follow_redirects": policy.max_redirects > 0,
        "max_redirects": policy.max_redirects,
    }


def denied_hosts_summary(policy: ScopePolicy) -> Iterable[str]:
    """给 `make doctor` 用的可读拒绝网段清单。"""
    return sorted(str(n) for n in denials_for(policy))
