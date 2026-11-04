"""B55 插件端到端（`H03 §7` / `H07` B55）。

判据：装前预演 impact · 装完可用 · 升级失败回滚到旧版 · **卸载后工具面清单逐字相同**。
最后一条是本批的核心：清单相同是可断言的，不是"看起来干净了"。
"""

from __future__ import annotations

import pytest

from veripen_core.plugins.lifecycle import Lifecycle, PluginError, PluginSpec
from veripen_core.tools.surface import Impact, SurfaceError, build_surface


def _spec(**kw: object) -> PluginSpec:
    base: dict[str, object] = dict(
        name="burp", version="1.0.0", mount="act",
        tool_name="plugin.burp.scan", impact=Impact.EXEC,
    )
    base.update(kw)
    return PluginSpec(**base)  # type: ignore[arg-type]


def _lc() -> Lifecycle:
    return Lifecycle(surface=build_surface())


# ── A 装前预演 ─────────────────────────────────────────────────
def test_preview_reports_impact_and_approval_need() -> None:
    info = _lc().preview(_spec())
    assert info["impact"] == "exec" and info["needs_approval"] is True
    assert info["will_expose_tool"] == "plugin.burp.scan"


def test_knowledge_plugin_cannot_expose_tool() -> None:
    with pytest.raises(PluginError) as err:
        _lc().install(_spec(mount="knowledge"))
    assert err.value.code == "knowledge_cannot_expose_tool"


def test_tool_must_stay_in_namespace() -> None:
    with pytest.raises(PluginError) as err:
        _lc().install(_spec(tool_name="fs.write"))
    assert err.value.code == "tool_namespace"


# ── B 装完可用 ─────────────────────────────────────────────────
def test_install_then_call_goes_through_surface_gate() -> None:
    lc = _lc()
    lc.install(_spec(), handler=lambda args: {"ok": True, "echo": args.get("x")})
    with pytest.raises(SurfaceError) as err:
        lc.surface.invoke("plugin.burp.scan", {"x": 1})
    assert err.value.code == "approval_required"
    out = lc.surface.invoke("plugin.burp.scan", {"x": 1}, approved=True)
    assert out["echo"] == 1


def test_install_twice_is_refused() -> None:
    lc = _lc()
    lc.install(_spec())
    with pytest.raises(PluginError) as err:
        lc.install(_spec())
    assert err.value.code == "already_installed"


# ── C 升级失败回滚 ─────────────────────────────────────────────
def test_upgrade_failure_keeps_old_version_and_catalog() -> None:
    lc = _lc()
    lc.install(_spec(), handler=lambda args: {"v": "1.0.0"})
    before = lc.surface.catalog()

    def bad_check(_spec: PluginSpec) -> None:
        raise PluginError("bench_failed", "新版本没通过用例")

    with pytest.raises(PluginError):
        lc.upgrade(_spec(version="2.0.0"), check=bad_check)
    assert lc.installed["burp"].version == "1.0.0"
    assert lc.surface.catalog() == before, "升级失败不许留下半装的工具"
    assert lc.surface.invoke("plugin.burp.scan", {}, approved=True)["v"] == "1.0.0"


def test_upgrade_cannot_change_impact_or_tool_name() -> None:
    lc = _lc()
    lc.install(_spec())
    with pytest.raises(PluginError) as err:
        lc.upgrade(_spec(version="2.0.0", impact=Impact.READ))
    assert err.value.code == "identity_drift"


def test_successful_upgrade_bumps_version() -> None:
    lc = _lc()
    lc.install(_spec(), handler=lambda args: {"v": "1"})
    lc.upgrade(_spec(version="1.1.0"), handler=lambda args: {"v": "1.1"})
    assert lc.installed["burp"].version == "1.1.0"
    assert lc.surface.invoke("plugin.burp.scan", {}, approved=True)["v"] == "1.1"


# ── D 卸载后清单逐字相同 ───────────────────────────────────────
def test_uninstall_restores_catalog_exactly() -> None:
    lc = _lc()
    before = lc.surface.catalog()
    lc.install(_spec(knowledge=("doc-1",), config={"timeout_s": 30}))
    assert lc.surface.catalog() != before
    lc.uninstall("burp")
    assert lc.surface.catalog() == before, "卸载后必须逐字回到装前"
    assert "burp" not in lc.installed and "burp" not in lc.knowledge and "burp" not in lc.config


def test_uninstall_unknown_plugin_is_refused() -> None:
    with pytest.raises(PluginError) as err:
        _lc().uninstall("ghost")
    assert err.value.code == "not_installed"


def test_cannot_unregister_builtin_tools() -> None:
    surface = build_surface()
    with pytest.raises(SurfaceError) as err:
        surface.unregister("fs.read")
    assert err.value.code == "not_plugin_tool"


def test_knowledge_plugin_install_and_uninstall_is_clean() -> None:
    lc = _lc()
    before = lc.surface.catalog()
    lc.install(PluginSpec(name="kb", version="1.0.0", mount="knowledge",
                          knowledge=("doc-a", "doc-b")))
    assert lc.knowledge["kb"] == ("doc-a", "doc-b")
    assert lc.surface.catalog() == before, "知识包不该改动工具面"
    lc.uninstall("kb")
    assert lc.knowledge == {} and lc.surface.catalog() == before
