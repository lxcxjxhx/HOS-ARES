"""B12.11 · 插件注册层（36 §3）：已装清单 + 版本锁定 + 带冷却的更新检查。

这一层的全部价值在于 **lock.json 让"可复现"不是空话**：

> 一条 finding 诞生时用的是 ``burp-mcp@1.2.3``，三个月后上游发到 ``1.2.9``——
> 重跑时必须能拿回 ``1.2.3``。

四条更新纪律，每条都容易做成"看起来对"：

| 规则 | 值 | 不做会怎样 |
|---|---|---|
| 冷却 | ``last_checked`` < 6h → 跳过 | 狂点刷爆 API，把自己封了 |
| 响应复用 | 本轮发现层已打过同 host → 用同批响应 | 同一份数据跑两遍 |
| 只写 ``latest_known`` | 不改 ``installed_version`` | 安装变成隐式副作用 |
| 上游消失 | ``upstream_state=deleted``，**不自动卸载** | 用户正在用它跑任务 |

**请求预算是这里的闸门**：一次刷新最多 2 个请求，超了直接抛——不是提示。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Iterable, Mapping

#: 冷却窗口（36 §3.2）：``last_checked`` 距今 < 6h 直接跳过
COOLDOWN_S = 6 * 3600
#: 一次刷新最多几个请求（21 §B12.11 的 DoD：≤2）
MAX_REFRESH_REQUESTS = 2


class RegistryError(RuntimeError):
    code = "VP-8011"


class Mount(str, Enum):
    """四个挂载点（36 §4.1）。**只有 oracle 能碰判定**，这是整个插件体系的安全边界。"""

    KNOWLEDGE = "knowledge"
    ACT = "act"
    SOURCE = "source"
    ORACLE = "oracle"

    @property
    def may_judge(self) -> bool:
        return self is Mount.ORACLE


class Trust(str, Enum):
    AUTO = "auto"
    REVIEWED = "reviewed"
    SIGNED = "signed"


class UpstreamState(str, Enum):
    PRESENT = "present"
    DELETED = "deleted"
    UNREACHABLE = "unreachable"


@dataclass(frozen=True)
class Source:
    type: str
    repo: str = ""
    commit: str = ""
    registry: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"type": self.type, "repo": self.repo, "commit": self.commit,
                "registry": self.registry}

    @property
    def host_key(self) -> str:
        """用于"同批响应复用"的键：同一上游 = 同一批请求。"""
        if self.registry:
            return f"registry:{self.registry}"
        return f"github:{self.repo}" if self.repo else f"{self.type}:{self.commit}"


@dataclass(frozen=True)
class PluginRecord:
    id: str
    kind: str
    scenario: Mount
    installed_version: str
    source: Source
    install_method: str = "raw"
    latest_known: str | None = None
    enabled: bool = True
    installed_at: str = ""
    last_checked: str = ""
    upstream_state: UpstreamState = UpstreamState.PRESENT
    trust: Trust = Trust.AUTO

    @property
    def may_judge(self) -> bool:
        """挂载点决定权限。``scenario`` 不是 oracle 的，永远不能出结论。"""
        return self.scenario.may_judge

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "kind": self.kind, "scenario": self.scenario.value,
            "installed_version": self.installed_version,
            "latest_known": self.latest_known,
            "source": self.source.as_dict(),
            "install_method": self.install_method, "enabled": self.enabled,
            "installed_at": self.installed_at, "last_checked": self.last_checked,
            "upstream_state": self.upstream_state.value, "trust": self.trust.value,
        }


@dataclass(frozen=True)
class LockPin:
    """一条版本锁。``sha256`` 是**内容**指纹，不是版本号——版本可以重发，内容不行。"""

    resolved: str
    sha256: str

    def as_dict(self) -> dict[str, str]:
        return {"resolved": self.resolved, "sha256": self.sha256}


def lock_sha(pins: Mapping[str, Any]) -> str:
    """按 pin 字典重算锁快照的 sha256（B175）。

    `doctor` 用它**独立复算**一遍，而不是相信快照里现成的 `snapshot_sha256`——
    只检查"这个字段有 64 位"是永远绿的，什么也没验。
    """
    payload = json.dumps(
        {str(k): v for k, v in sorted(pins.items())},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_ts(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


class Registry:
    """``registry.json`` + ``lock.json`` 的内存视图。

    **两条不可越过的界线**：
    1. 更新检查只写 ``latest_known``，绝不改 ``installed_version``；
    2. 上游消失只标 ``deleted``，**不自动卸载**。
    """

    def __init__(self) -> None:
        self._plugins: dict[str, PluginRecord] = {}
        self._lock: dict[str, LockPin] = {}
        self.requests = 0

    # ── 注册与锁 ────────────────────────────────────────────────
    def install(self, rec: PluginRecord, *, content_sha256: str, now: datetime) -> LockPin:
        """安装 = 写清单 + 写锁。**锁是安装的一部分，不是可选项。**"""
        if not content_sha256 or len(content_sha256) != 64:
            raise RegistryError(f"{rec.id}: 锁需要 64 位内容 sha256，收到 {content_sha256!r}")
        self._plugins[rec.id] = rec
        pin = LockPin(resolved=rec.installed_version, sha256=content_sha256)
        self._lock[rec.id] = pin
        return pin

    def get(self, pid: str) -> PluginRecord | None:
        return self._plugins.get(pid)

    def all(self) -> list[PluginRecord]:
        return sorted(self._plugins.values(), key=lambda p: p.id)

    def lock_of(self, pid: str) -> LockPin | None:
        return self._lock.get(pid)

    def rollback(self, pid: str) -> str:
        """回到 ``lock.json`` 里的 resolved 版本。**读锁，不改锁。**"""
        pin = self._lock.get(pid)
        rec = self._plugins.get(pid)
        if pin is None or rec is None:
            raise RegistryError(f"{pid}: 没有锁记录，无法回滚（锁是安装时写的）")
        self._plugins[pid] = PluginRecord(**{**rec.__dict__, "installed_version": pin.resolved})
        return pin.resolved

    def snapshot_lock(self) -> dict[str, Any]:
        """任务开始时的 lock 快照。

        **这是"任何一次任务都能回到当时的插件版本"的实现点**：任务把这份快照写进 capsule，
        三个月后上游变了，仍能按快照取回当时的 resolved + sha256。
        """
        return {
            "version": 1,
            "lock": {k: v.as_dict() for k, v in sorted(self._lock.items())},
            "snapshot_sha256": self._lock_sha(),
        }

    def _lock_sha(self) -> str:
        return lock_sha({k: v.as_dict() for k, v in sorted(self._lock.items())})

    # ── 更新检查（带冷却 + 响应复用）─────────────────────────────
    def due_for_check(self, pid: str, *, now: datetime) -> bool:
        """冷却：``last_checked`` 距今 < 6h → 不必查。"""
        rec = self._plugins.get(pid)
        if rec is None:
            raise RegistryError(f"{pid} 未安装")
        last = _parse_ts(rec.last_checked)
        if last is None:
            return True
        return (now - last) >= timedelta(seconds=COOLDOWN_S)

    def update_check(
        self,
        ids: Iterable[str],
        *,
        now: datetime,
        fetch: Mapping[str, Mapping[str, Any]] | None = None,
        already_checked_hosts: frozenset[str] = frozenset(),
    ) -> tuple[dict[str, Any], int]:
        """只查不装。返回 ``(事件列表, 实际发出的请求数)``。

        ``fetch`` 是本轮**可复用的响应**（``{host_key: {"version":..., "state":...}}``）；
        ``already_checked_hosts`` 里出现过的主机不再请求——这就是 36 §3.2 的"不跑两遍"。
        """
        fetch = fetch or {}
        events: dict[str, Any] = {}
        requests = 0
        #: 本次刷新里**已经花过请求**的 host。同一上游只花一次——
        #: 只比对 ``already_checked_hosts``（历史）而忘了记本轮的，就会对同 host 重复计数。
        spent_hosts: set[str] = set()

        for pid in sorted(ids):
            rec = self._plugins.get(pid)
            if rec is None:
                raise RegistryError(f"{pid} 未安装")
            if not self.due_for_check(pid, now=now):
                events[pid] = {"skipped": "cooldown"}
                continue

            host = rec.source.host_key
            reusable = host in fetch or host in already_checked_hosts or host in spent_hosts
            if not reusable:
                requests += 1
                if requests > MAX_REFRESH_REQUESTS:
                    raise RegistryError(
                        f"一次刷新最多 {MAX_REFRESH_REQUESTS} 个请求（21 §B12.11），"
                        f"处理到 {pid} 时超了"
                    )
                spent_hosts.add(host)

            reply = fetch.get(host) or {}
            if reply.get("unreachable"):
                # 断网不等于"没更新"：保留上次结论，只标 unreachable
                self._plugins[pid] = PluginRecord(
                    **{**rec.__dict__, "upstream_state": UpstreamState.UNREACHABLE,
                       "last_checked": now.isoformat()}
                )
                events[pid] = {"upstream_state": "unreachable"}
                continue

            latest = reply.get("version")
            state = UpstreamState(reply.get("state", UpstreamState.PRESENT.value))
            self._plugins[pid] = PluginRecord(
                **{
                    **rec.__dict__,
                    # **只写 latest_known**：安装是显式动作
                    "latest_known": latest or rec.latest_known,
                    "last_checked": now.isoformat(),
                    "upstream_state": state,
                }
            )
            events[pid] = {
                "latest_known": latest,
                "upstream_state": state.value,
                "update_available": bool(latest and latest != rec.installed_version),
                # 上游消失也不动 installed_version
                "still_installed": rec.installed_version,
            }

        self.requests = requests
        return events, requests

    # ── 序列化 ──────────────────────────────────────────────────
    def to_json(self) -> dict[str, Any]:
        return {
            "version": 1,
            "plugins": [p.as_dict() for p in self.all()],
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, Any], *, lock: Mapping[str, Any] | None = None) -> "Registry":
        reg = cls()
        for item in raw.get("plugins") or ():
            src = item.get("source") or {}
            rec = PluginRecord(
                id=str(item["id"]),
                kind=str(item.get("kind") or "skill"),
                scenario=Mount(str(item.get("scenario") or "knowledge")),
                installed_version=str(item.get("installed_version") or "0.0.0"),
                source=Source(
                    type=str(src.get("type") or "github"),
                    repo=str(src.get("repo") or ""),
                    commit=str(src.get("commit") or ""),
                    registry=str(src.get("registry") or ""),
                ),
                install_method=str(item.get("install_method") or "raw"),
                latest_known=(str(item["latest_known"]) if item.get("latest_known") else None),
                enabled=bool(item.get("enabled", True)),
                installed_at=str(item.get("installed_at") or ""),
                last_checked=str(item.get("last_checked") or ""),
                upstream_state=UpstreamState(str(item.get("upstream_state") or "present")),
                trust=Trust(str(item.get("trust") or "auto")),
            )
            reg._plugins[rec.id] = rec
        for pid, pin in ((lock or {}).get("lock") or {}).items():
            reg._lock[str(pid)] = LockPin(
                resolved=str(pin.get("resolved") or ""), sha256=str(pin.get("sha256") or "")
            )
        return reg


def assert_no_oracle_drift(reg: Registry) -> None:
    """装配前自检：**非 oracle 挂载点不得带判定权限**。

    这条在 B12.12 会被反复调用；放在这里是因为它只依赖注册层的数据，
    依赖装配层反而会把简单检查做成循环依赖。
    """
    bad = [p.id for p in reg.all() if p.may_judge and p.scenario is not Mount.ORACLE]
    if bad:  # pragma: no cover - 类型上不可达，留给将来有人改 may_judge 时兜底
        raise RegistryError(f"{bad}: 非 oracle 挂载点声称能判定")


__all__ = [
    "COOLDOWN_S",
    "MAX_REFRESH_REQUESTS",
    "LockPin",
    "Mount",
    "PluginRecord",
    "Registry",
    "RegistryError",
    "Source",
    "Trust",
    "UpstreamState",
    "assert_no_oracle_drift",
]
