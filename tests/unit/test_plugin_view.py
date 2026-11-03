"""B12.13 插件 UI 渲染契约测试（36 §6）。

这批**不碰 HTML**：先有能被渲染的数据，再画画。所以测的是契约本身。

三条"UI 必须体现"的东西，各有用例：
- 缓存时间 + 下次自动检查 → 用户知道看到的不是实时数据；
- 版本箭头 → 一眼看出哪些要更新；
- 挂载点色标 → 风险等级可视化（**色标必须配风险说明**，否则只是装饰）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from veripen_core.plugins.discovery import DEFAULT_TTL_S, CacheEntry, Freshness, Result
from veripen_core.plugins.registry import (
    Mount,
    PluginRecord,
    Source,
    Trust,
    UpstreamState,
)
from veripen_core.plugins.view import (
    MOUNT_COLORS,
    MOUNT_RISK,
    build_tab,
    freshness_bar,
    install_row,
    installed_row,
    mount_badge,
    mount_color,
    stale_hint,
    version_arrow,
)

NOW = datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc)


def _rec(**kw) -> PluginRecord:  # noqa: ANN003
    base = dict(
        id="gh:o/burp-mcp", kind="mcp", scenario=Mount.ACT, installed_version="1.2.3",
        source=Source(type="github", repo="o/burp-mcp", commit="abc"),
        install_method="mcp", installed_at=NOW.isoformat(),
        last_checked=(NOW - timedelta(hours=2)).isoformat(), trust=Trust.REVIEWED,
    )
    base.update(kw)
    return PluginRecord(**base)  # type: ignore[arg-type]


def _entry(*, age_s: int = 720) -> CacheEntry:  # noqa: ANN001
    from veripen_core.plugins.discovery import Result as R

    return CacheEntry(
        query="skill:pentest",
        results=[R(id="gh:a/b", kind="skill", repo="a/b", stars=10,
                   pushed_at="2026-10-30")],
        fetched_at=NOW - timedelta(seconds=age_s), ttl_s=DEFAULT_TTL_S,
    )


# ══════════════════════════════════════════════════════════════════
# 1 色标：绿 / 黄 / 橙 / 红，且必须配风险说明
# ══════════════════════════════════════════════════════════════════
def test_mount_colors_follow_risk_order() -> None:
    assert mount_color(Mount.KNOWLEDGE) == "green"
    assert mount_color(Mount.ACT) == "yellow"
    assert mount_color(Mount.SOURCE) == "orange"
    assert mount_color(Mount.ORACLE) == "red"


def test_every_mount_has_a_color_and_a_risk_sentence() -> None:
    for m in Mount:
        badge = mount_badge(m)
        assert badge["color"] in {"green", "yellow", "orange", "red"}
        assert badge["risk"].strip(), f"{m} 有色标却没有风险说明——那就只是装饰"
        assert badge["may_judge"] in {"yes", "no"}


def test_only_oracle_badge_says_may_judge() -> None:
    assert mount_badge(Mount.ORACLE)["may_judge"] == "yes"
    for m in (Mount.KNOWLEDGE, Mount.ACT, Mount.SOURCE):
        assert mount_badge(m)["may_judge"] == "no"


def test_unknown_mount_raises() -> None:
    with pytest.raises(Exception):
        mount_color("nope")  # type: ignore[arg-type]


def test_color_maps_cover_exactly_the_four_mounts() -> None:
    assert set(MOUNT_COLORS) == set(Mount)
    assert set(MOUNT_RISK) == set(Mount)


# ══════════════════════════════════════════════════════════════════
# 2 新鲜度条
# ══════════════════════════════════════════════════════════════════
def test_freshness_bar_shows_cache_age_and_next_check() -> None:
    bar = freshness_bar(_entry(age_s=720), now=NOW)
    assert bar.state == Freshness.FRESH.value
    assert bar.cached_ago == "12 分钟前"
    assert bar.next_check_in  # 非空
    assert bar.warns_staleness is False


def test_stale_cache_warns_explicitly() -> None:
    """过期必须显式警告——否则用户把旧列表当实时结果。"""
    bar = freshness_bar(_entry(age_s=DEFAULT_TTL_S + 600), now=NOW)
    assert bar.state == Freshness.POSSIBLY_STALE.value
    assert bar.warns_staleness is True


def test_absent_cache_says_never() -> None:
    e = _entry()
    e.fetched_at = None
    bar = freshness_bar(e, now=NOW)
    assert bar.state == Freshness.ABSENT.value
    assert bar.cached_ago == "从未"
    assert bar.warns_staleness is True


def test_api_budget_is_visible() -> None:
    bar = freshness_bar(_entry(), now=NOW, api_used=1, api_budget=2)
    assert bar.as_dict()["api"] == "1/2"


def test_freshness_bar_serialises_flat() -> None:
    d = freshness_bar(_entry(), now=NOW).as_dict()
    assert set(d) == {"state", "cached_ago", "next_check_in", "api", "stale_warning"}


# ══════════════════════════════════════════════════════════════════
# 3 版本箭头
# ══════════════════════════════════════════════════════════════════
def test_version_arrow_shows_update() -> None:
    assert version_arrow(_rec(latest_known="1.2.5")) == "v1.2.3 → 1.2.5 可用"


def test_version_arrow_shows_latest() -> None:
    assert version_arrow(_rec(latest_known="1.2.3")) == "v1.2.3（最新）"
    assert version_arrow(_rec(latest_known=None)) == "v1.2.3（最新）"


def test_version_arrow_shows_deleted_upstream() -> None:
    assert "已删除" in version_arrow(_rec(upstream_state=UpstreamState.DELETED))


# ══════════════════════════════════════════════════════════════════
# 4 已安装行：按钮由状态推出
# ══════════════════════════════════════════════════════════════════
def test_installed_row_has_update_button_when_newer() -> None:
    row = installed_row(_rec(latest_known="1.2.5"))
    assert "更新" in row["actions"]
    assert row["color"] == "yellow"


def test_installed_row_without_update_has_no_update_button() -> None:
    row = installed_row(_rec(latest_known="1.2.3"))
    assert "更新" not in row["actions"]


def test_deleted_upstream_offers_keep_not_auto_uninstall() -> None:
    """上游删了也不自动卸载：给「保留」，卸载是用户的选择。"""
    row = installed_row(_rec(upstream_state=UpstreamState.DELETED))
    assert row["actions"] == ["保留", "卸载"]


def test_oracle_row_is_red() -> None:
    row = installed_row(_rec(scenario=Mount.ORACLE, id="gh:o/my-oracle"))
    assert row["color"] == "red"
    assert row["risk"]


def test_installed_row_always_has_risk_text() -> None:
    for m in Mount:
        row = installed_row(_rec(scenario=m, id=f"gh:o/{m.value}"))
        assert row["risk"].strip()


# ══════════════════════════════════════════════════════════════════
# 5 可安装行：⚠ 提示由挂载点推出，不由 README 决定
# ══════════════════════════════════════════════════════════════════
def test_act_install_label_warns_sandbox() -> None:
    row = install_row(plugin_id="gh:a/b", stars=1200, license="Apache-2.0",
                      pushed_at="2026-10-20", mount=Mount.ACT, install_method="mcp")
    assert "⚠需沙箱" in row["install_label"]


def test_oracle_install_label_warns_bench() -> None:
    row = install_row(plugin_id="gh:a/o", stars=10, license="MIT", pushed_at="2026-10-20",
                      mount=Mount.ORACLE, install_method="raw")
    assert "⚠需 44 例 bench" in row["install_label"]


def test_knowledge_install_has_no_warning() -> None:
    row = install_row(plugin_id="gh:a/k", stars=3200, license="MIT", pushed_at="2026-10-30",
                      mount=Mount.KNOWLEDGE, install_method="raw")
    assert "⚠" not in row["install_label"]
    assert row["install_label"] == "装为 knowledge"
    assert row["color"] == "green"


def test_missing_license_is_shown_as_none_not_empty() -> None:
    row = install_row(plugin_id="gh:a/b", stars=1, license=None, pushed_at="2026-10-30",
                      mount=Mount.KNOWLEDGE, install_method="raw")
    assert row["license"] == "无"


# ══════════════════════════════════════════════════════════════════
# 6 整页
# ══════════════════════════════════════════════════════════════════
def test_tab_has_freshness_legend_and_both_lists() -> None:
    view = build_tab(entry=_entry(), installed=[_rec(), _rec(id="gh:o/k", scenario=Mount.KNOWLEDGE)],
                     now=NOW)
    d = view.as_dict()
    assert d["tab"] == "插件"
    assert d["counts"] == {"installable": 0, "installed": 2}
    assert len(d["legend"]) == 4
    assert {x["mount"] for x in d["legend"]} == {m.value for m in Mount}


def test_installed_list_is_sorted_by_id() -> None:
    view = build_tab(entry=_entry(),
                     installed=[_rec(id="gh:zzz"), _rec(id="gh:aaa")], now=NOW)
    assert [r["id"] for r in view.installed] == ["gh:aaa", "gh:zzz"]


def test_stale_hint_says_what_happens_next() -> None:
    """提示要说清下一步，不是"可能已变"这种含糊话。"""
    stale = freshness_bar(_entry(age_s=DEFAULT_TTL_S + 60), now=NOW)
    assert "后台" in stale_hint(stale)
    ok = freshness_bar(_entry(), now=NOW)
    assert stale_hint(ok) == ""


def test_tab_roundtrips_through_json() -> None:
    import json

    d = build_tab(entry=_entry(), installed=[_rec()], now=NOW).as_dict()
    assert json.loads(json.dumps(d, ensure_ascii=False))["counts"]["installed"] == 1
