"""B12.11 插件注册层测试（36 §3）。

五条 DoD：
- 一次刷新请求 ≤2；
- 冷却 6h 内跳过；
- 只写 latest_known，不改 installed_version；
- 上游消失标 deleted，**不自动卸载**；
- lock.json 让"回到当时的版本"可执行（rollback + snapshot）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from veripen_core.plugins.registry import (
    COOLDOWN_S,
    MAX_REFRESH_REQUESTS,
    Mount,
    PluginRecord,
    Registry,
    RegistryError,
    Source,
    Trust,
    UpstreamState,
    assert_no_oracle_drift,
)

NOW = datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc)
SHA = "a" * 64


def _rec(pid: str, *, scenario: Mount = Mount.KNOWLEDGE, version: str = "1.0.0",
         last_checked: str = "", host: str = "owner/repo") -> PluginRecord:  # noqa: ANN001
    return PluginRecord(
        id=pid, kind="skill", scenario=scenario, installed_version=version,
        source=Source(type="github", repo=host, commit="abc1234"),
        install_method="raw", installed_at=NOW.isoformat(), last_checked=last_checked,
        trust=Trust.REVIEWED,
    )


def _reg(*recs: PluginRecord) -> Registry:  # noqa: ANN001
    reg = Registry()
    for r in recs:
        reg.install(r, content_sha256=SHA, now=NOW)
    return reg


# ══════════════════════════════════════════════════════════════════
# 1 安装即写锁
# ══════════════════════════════════════════════════════════════════
def test_install_writes_a_lock_pin() -> None:
    reg = _reg(_rec("gh:o/r"))
    pin = reg.lock_of("gh:o/r")
    assert pin is not None
    assert pin.resolved == "1.0.0"
    assert pin.sha256 == SHA


def test_install_without_content_hash_is_rejected() -> None:
    """锁是安装的一部分，不是可选项：没有内容指纹就没有可复现。"""
    reg = Registry()
    with pytest.raises(RegistryError, match="64 位内容 sha256"):
        reg.install(_rec("gh:o/r"), content_sha256="", now=NOW)
    with pytest.raises(RegistryError, match="64 位内容 sha256"):
        reg.install(_rec("gh:o/r"), content_sha256="short", now=NOW)


def test_rollback_returns_to_locked_version() -> None:
    reg = _reg(_rec("gh:o/r", version="1.2.3"))
    # 模拟升级到 1.2.9（只改清单，不动锁）
    reg.install(_rec("gh:o/r", version="1.2.3"), content_sha256=SHA, now=NOW)
    reg._plugins["gh:o/r"] = PluginRecord(
        **{**reg.get("gh:o/r").__dict__, "installed_version": "1.2.9"}  # type: ignore[union-attr]
    )
    assert reg.rollback("gh:o/r") == "1.2.3"
    assert reg.get("gh:o/r").installed_version == "1.2.3"  # type: ignore[union-attr]


def test_rollback_without_lock_is_rejected() -> None:
    reg = Registry()
    with pytest.raises(RegistryError, match="没有锁记录"):
        reg.rollback("gh:nope")


def test_lock_snapshot_is_stable_and_hashed() -> None:
    """任务开始时的 lock 快照：三次调用哈希相同。"""
    reg = _reg(_rec("gh:a/b"), _rec("gh:c/d"))
    s1 = reg.snapshot_lock()
    s2 = reg.snapshot_lock()
    assert s1 == s2
    assert len(s1["snapshot_sha256"]) == 64
    assert s1["snapshot_sha256"] == s2["snapshot_sha256"]


def test_lock_snapshot_changes_when_lock_changes() -> None:
    reg = _reg(_rec("gh:a/b"))
    before = reg.snapshot_lock()["snapshot_sha256"]
    reg.install(_rec("gh:c/d"), content_sha256="b" * 64, now=NOW)
    assert reg.snapshot_lock()["snapshot_sha256"] != before


# ══════════════════════════════════════════════════════════════════
# 2 冷却：6h 内跳过
# ══════════════════════════════════════════════════════════════════
def test_cooldown_skips_check_within_six_hours() -> None:
    reg = _reg(_rec("gh:o/r", last_checked=(NOW - timedelta(hours=5)).isoformat()))
    events, reqs = reg.update_check(["gh:o/r"], now=NOW)
    assert events["gh:o/r"] == {"skipped": "cooldown"}
    assert reqs == 0


def test_check_allowed_after_cooldown() -> None:
    reg = _reg(_rec("gh:o/r", last_checked=(NOW - timedelta(seconds=COOLDOWN_S + 60)).isoformat()))
    assert reg.due_for_check("gh:o/r", now=NOW) is True


def test_never_checked_is_due() -> None:
    reg = _reg(_rec("gh:o/r", last_checked=""))
    assert reg.due_for_check("gh:o/r", now=NOW) is True


def test_cooldown_window_is_six_hours() -> None:
    assert COOLDOWN_S == 6 * 3600


# ══════════════════════════════════════════════════════════════════
# 3 一次刷新请求 ≤2
# ══════════════════════════════════════════════════════════════════
def test_refresh_request_budget() -> None:
    reg = _reg(
        _rec("gh:a/1", host="a/1"), _rec("gh:a/2", host="a/2"), _rec("gh:a/3", host="a/3"),
    )
    with pytest.raises(RegistryError, match="最多 2 个请求"):
        reg.update_check(["gh:a/1", "gh:a/2", "gh:a/3"], now=NOW)
    assert MAX_REFRESH_REQUESTS == 2


def test_two_hosts_fit_in_budget() -> None:
    reg = _reg(_rec("gh:a/1", host="a/1"), _rec("gh:a/2", host="a/2"))
    _, reqs = reg.update_check(["gh:a/1", "gh:a/2"], now=NOW)
    assert reqs == 2


def test_same_host_costs_one_request() -> None:
    """同上游的两个插件只花一个请求（响应复用）。"""
    reg = _reg(_rec("gh:a/1", host="same/repo"), _rec("gh:a/2", host="same/repo"))
    _, reqs = reg.update_check(["gh:a/1", "gh:a/2"], now=NOW)
    assert reqs == 1


def test_reused_fetch_costs_zero_requests() -> None:
    """本轮发现层已打过同 host → 用同批响应，0 请求。"""
    reg = _reg(_rec("gh:a/1", host="a/1"), _rec("gh:a/2", host="a/2"))
    fetch = {"github:a/1": {"version": "9.9.9"}, "github:a/2": {"version": "8.8.8"}}
    _, reqs = reg.update_check(["gh:a/1", "gh:a/2"], now=NOW,
                               already_checked_hosts=frozenset(fetch))
    assert reqs == 0


# ══════════════════════════════════════════════════════════════════
# 4 只写 latest_known
# ══════════════════════════════════════════════════════════════════
def test_update_check_only_writes_latest_known() -> None:
    reg = _reg(_rec("gh:o/r", version="1.2.3", host="o/r"))
    events, _ = reg.update_check(["gh:o/r"], now=NOW,
                                 fetch={"github:o/r": {"version": "1.2.9"}})
    rec = reg.get("gh:o/r")
    assert rec is not None
    assert rec.installed_version == "1.2.3"   # 安装是显式动作
    assert rec.latest_known == "1.2.9"
    assert events["gh:o/r"]["update_available"] is True


def test_no_update_available_flag_when_same_version() -> None:
    reg = _reg(_rec("gh:o/r", version="1.2.3", host="o/r"))
    events, _ = reg.update_check(["gh:o/r"], now=NOW,
                                 fetch={"github:o/r": {"version": "1.2.3"}})
    assert events["gh:o/r"]["update_available"] is False


def test_upstream_deleted_does_not_uninstall() -> None:
    """用户可能正在用它跑任务：标 deleted，不动 installed_version。"""
    reg = _reg(_rec("gh:o/r", version="1.2.3", host="o/r"))
    events, _ = reg.update_check(["gh:o/r"], now=NOW,
                                 fetch={"github:o/r": {"state": "deleted"}})
    rec = reg.get("gh:o/r")
    assert rec is not None
    assert rec.upstream_state is UpstreamState.DELETED
    assert rec.installed_version == "1.2.3"
    assert events["gh:o/r"]["still_installed"] == "1.2.3"


def test_unreachable_keeps_previous_conclusion() -> None:
    """断网不等于"没更新"：保留上次 latest_known，只标 unreachable。"""
    reg = _reg(_rec("gh:o/r", version="1.2.3", host="o/r"))
    reg.update_check(["gh:o/r"], now=NOW, fetch={"github:o/r": {"version": "1.5.0"}})
    later = NOW + timedelta(hours=7)
    events, _ = reg.update_check(["gh:o/r"], now=later,
                                 fetch={"github:o/r": {"unreachable": True}})
    rec = reg.get("gh:o/r")
    assert rec is not None
    assert rec.upstream_state is UpstreamState.UNREACHABLE
    assert rec.latest_known == "1.5.0"   # 上次结论还在
    assert events["gh:o/r"] == {"upstream_state": "unreachable"}


def test_last_checked_is_stamped() -> None:
    reg = _reg(_rec("gh:o/r", host="o/r"))
    reg.update_check(["gh:o/r"], now=NOW, fetch={})
    assert reg.get("gh:o/r").last_checked.startswith("2026-11-01")  # type: ignore[union-attr]


def test_unknown_plugin_is_rejected() -> None:
    reg = Registry()
    with pytest.raises(RegistryError, match="未安装"):
        reg.update_check(["gh:nope"], now=NOW)


# ══════════════════════════════════════════════════════════════════
# 5 挂载点：只有 oracle 能判定
# ══════════════════════════════════════════════════════════════════
def test_only_oracle_mount_may_judge() -> None:
    for m in Mount:
        assert m.may_judge is (m is Mount.ORACLE)


def test_knowledge_plugin_cannot_judge() -> None:
    rec = _rec("gh:o/notes", scenario=Mount.KNOWLEDGE)
    assert rec.may_judge is False


def test_assert_no_oracle_drift_passes_on_clean_registry() -> None:
    reg = _reg(_rec("gh:a/k", scenario=Mount.KNOWLEDGE),
               _rec("gh:a/o", scenario=Mount.ORACLE))
    assert_no_oracle_drift(reg)  # 不抛


# ══════════════════════════════════════════════════════════════════
# 6 序列化往返
# ══════════════════════════════════════════════════════════════════
def test_registry_roundtrips() -> None:
    reg = _reg(_rec("gh:a/b", scenario=Mount.ORACLE, version="2.0.1"))
    blob = reg.to_json()
    assert blob["version"] == 1
    back = Registry.from_json(blob, lock=reg.snapshot_lock())
    rec = back.get("gh:a/b")
    assert rec is not None
    assert rec.scenario is Mount.ORACLE
    assert rec.installed_version == "2.0.1"
    assert back.lock_of("gh:a/b") is not None


def test_registry_json_field_set_matches_doc() -> None:
    reg = _reg(_rec("gh:a/b"))
    item = reg.to_json()["plugins"][0]
    assert set(item) == {
        "id", "kind", "scenario", "installed_version", "latest_known", "source",
        "install_method", "enabled", "installed_at", "last_checked",
        "upstream_state", "trust",
    }
