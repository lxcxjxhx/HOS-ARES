"""B12.13 · 插件 UI 的**渲染数据契约**（36 §6）。

这一批**不碰 HTML**：B12.8 的 L1 页面主体还没画，先给不存在的页面加一个 Tab
只会产出假数据。所以这里只定"视图该拿什么、怎么分组、色标怎么映射"，
HTML/JS 等有真事件源时一次画完。

36 §6 说 UI 上必须体现三件事，否则用户会误判数据新鲜度：

| 元素 | 为什么 |
|---|---|
| 缓存时间 + 下次自动检查 | 让人知道看到的不是实时数据 |
| 版本箭头 ``1.2.3 → 1.2.5`` | 一眼看出哪些要更新 |
| 挂载点色标（绿/黄/橙/红） | 风险等级可视化 |

**色标不是装饰**：它是「这个插件能碰判定吗」的唯一可视化提示。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Mapping

from .discovery import CacheEntry, Freshness
from .registry import Mount, PluginRecord, UpstreamState

#: 挂载点色标（36 §6）：绿 / 黄 / 橙 / 红，按"能碰判定的程度"排
MOUNT_COLORS: Mapping[Mount, str] = {
    Mount.KNOWLEDGE: "green",
    Mount.ACT: "yellow",
    Mount.SOURCE: "orange",
    Mount.ORACLE: "red",
}

#: 色标对应的风险一句话。**UI 上必须显示**，不然红绿只是装饰。
MOUNT_RISK: Mapping[Mount, str] = {
    Mount.KNOWLEDGE: "只读文本，永不影响判定",
    Mount.ACT: "可执行，只能产线索，需沙箱",
    Mount.SOURCE: "能影响路由绑定，需人工 review",
    Mount.ORACLE: "能出判定结论，需签名 + 44 例 bench",
}


class ViewError(RuntimeError):
    code = "VP-8013"


def mount_color(mount: Mount) -> str:
    color = MOUNT_COLORS.get(mount)
    if color is None:
        raise ViewError(f"未知挂载点 {mount!r}")
    return color


def mount_badge(mount: Mount) -> dict[str, str]:
    """色标 + 风险说明。**两者必须一起出现**——只给颜色等于没给信息。"""
    return {
        "mount": mount.value,
        "color": mount_color(mount),
        "risk": MOUNT_RISK[mount],
        "may_judge": "yes" if mount.may_judge else "no",
    }


# ══════════════════════════════════════════════════════════════════
# 新鲜度条：让"看到的不是实时数据"可见
# ══════════════════════════════════════════════════════════════════
def _fmt_age(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return f"{s} 秒前"
    if s < 3600:
        return f"{s // 60} 分钟前"
    if s < 86400:
        return f"{s // 3600} 小时前"
    return f"{s // 86400} 天前"


def _fmt_ahead(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return "不到 1 分钟"
    if s < 3600:
        return f"{s // 60} 分钟"
    if s < 86400:
        return f"{s // 3600} 小时"
    return f"{s // 86400} 天"


@dataclass(frozen=True)
class FreshnessBar:
    """顶部那条「缓存 12 分钟前 · 下次自动检查 23:48 后」。"""

    state: str            # cached | possibly_stale | absent
    cached_ago: str       # 人读："12 分钟前"
    next_check_in: str    # 人读："23:48 后"
    api_used: int = 0
    api_budget: int = 0

    @property
    def warns_staleness(self) -> bool:
        """``possibly_stale`` 必须在 UI 上显式警告——否则用户把它当实时结果。"""
        return self.state != Freshness.FRESH.value

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state, "cached_ago": self.cached_ago,
            "next_check_in": self.next_check_in,
            "api": f"{self.api_used}/{self.api_budget}",
            "stale_warning": self.warns_staleness,
        }


def freshness_bar(
    entry: CacheEntry, *, now: datetime, api_used: int = 0, api_budget: int = 2
) -> FreshnessBar:
    state = entry.freshness(now)
    if entry.fetched_at is None:
        return FreshnessBar(state=state.value, cached_ago="从未", next_check_in="立即",
                            api_used=api_used, api_budget=api_budget)
    ago = (now - entry.fetched_at).total_seconds()
    ahead = (entry.next_check_at() - now).total_seconds()
    return FreshnessBar(
        state=state.value, cached_ago=_fmt_age(ago), next_check_in=_fmt_ahead(ahead),
        api_used=api_used, api_budget=api_budget,
    )


# ══════════════════════════════════════════════════════════════════
# 版本箭头：一眼看出哪些要更新
# ══════════════════════════════════════════════════════════════════
def version_arrow(rec: PluginRecord) -> str:
    """``v1.2.3 → 1.2.5 可用`` / ``v2.0.1（最新）`` / ``v0.9.0 · 上游已删除``。"""
    cur = rec.installed_version
    if rec.upstream_state is UpstreamState.DELETED:
        return f"v{cur} · 上游已删除"
    if rec.latest_known and rec.latest_known != cur:
        return f"v{cur} → {rec.latest_known} 可用"
    return f"v{cur}（最新）"


def installed_row(rec: PluginRecord, *, enabled: bool | None = None) -> dict[str, Any]:
    """已安装列表的一行。**按钮集合由色标与上游状态决定**，不由前端拍脑袋。"""
    actions: list[str] = ["查看"]
    if rec.latest_known and rec.latest_known != rec.installed_version:
        actions.append("更新")
    if enabled if enabled is not None else rec.enabled:
        actions.append("禁用")
    else:
        actions.append("启用")
    actions.append("回滚")
    if rec.upstream_state is UpstreamState.DELETED:
        # 上游删了也不自动卸载：用户可以保留，也可以自己卸
        actions = ["保留", "卸载"]

    badge = mount_badge(rec.scenario)
    return {
        "id": rec.id,
        "version": version_arrow(rec),
        "mount": badge["mount"],
        "color": badge["color"],
        "risk": badge["risk"],
        "upstream_state": rec.upstream_state.value,
        "last_checked": rec.last_checked,
        "trust": rec.trust.value,
        "actions": actions,
    }


def install_row(*, plugin_id: str, stars: int, license: str | None,
                pushed_at: str, mount: Mount, install_method: str,
                needs_sandbox: bool = False) -> dict[str, Any]:
    """可安装列表的一行。

    ``⚠需沙箱`` 这类提示**由挂载点推出**，不由搜索结果的文案决定——
    上游 README 说自己"安全"不是证据，挂载点才是。
    """
    badge = mount_badge(mount)
    label = f"装为 {mount.value}"
    if needs_sandbox or mount is Mount.ACT:
        label += " ⚠需沙箱"
    if mount is Mount.ORACLE:
        label += " ⚠需 44 例 bench"
    return {
        "id": plugin_id,
        "stars": stars,
        "license": license or "无",
        "pushed_at": pushed_at,
        "install_method": install_method,
        "mount": mount.value,
        "color": badge["color"],
        "risk": badge["risk"],
        "install_label": label,
        "actions": ["预览", label],
    }


# ══════════════════════════════════════════════════════════════════
# 整页视图
# ══════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class PluginTabView:
    freshness: FreshnessBar
    installable: list[dict[str, Any]] = field(default_factory=list)
    installed: list[dict[str, Any]] = field(default_factory=list)
    legend: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tab": "插件",
            "freshness": self.freshness.as_dict(),
            "counts": {
                "installable": len(self.installable),
                "installed": len(self.installed),
            },
            "legend": self.legend,
            "installable": self.installable,
            "installed": self.installed,
        }


def build_tab(
    *, entry: CacheEntry, installed: list[PluginRecord], now: datetime,
    installable: list[dict[str, Any]] | None = None, api_used: int = 0,
) -> PluginTabView:
    """第四个 Tab 的完整数据。**图例恒在**：用户第一次打开就该知道颜色什么意思。"""
    legend = [mount_badge(m) for m in Mount]
    return PluginTabView(
        freshness=freshness_bar(entry, now=now, api_used=api_used),
        installable=list(installable or []),
        installed=[installed_row(r) for r in sorted(installed, key=lambda r: r.id)],
        legend=legend,
    )


def stale_hint(bar: FreshnessBar) -> str:
    """过期提示文案。**不给"可能已变"这种含糊话**，要说清下一步。"""
    if bar.state == Freshness.POSSIBLY_STALE.value:
        return "缓存已过期，正在后台补最新数据；当前列表可能不是最新的"
    if bar.state == Freshness.ABSENT.value:
        return "本地还没有缓存，需要联网搜索一次"
    return ""


__all__ = [
    "MOUNT_COLORS",
    "MOUNT_RISK",
    "FreshnessBar",
    "PluginTabView",
    "ViewError",
    "build_tab",
    "freshness_bar",
    "install_row",
    "installed_row",
    "mount_badge",
    "mount_color",
    "stale_hint",
    "version_arrow",
]
