"""B558 · 插件生命周期：**失败回滚是真的回到旧版本**，不只是表格改回去。

`plugins/lifecycle.py` 已经写了 install / upgrade(失败回滚) / uninstall。
本批验五条判据，重点在**两条"失败路径"**上——它们最容易只改表格不改现场：

| 判据 | 为什么容易假绿 |
|---|---|
| 装完工具面出现该工具 | — |
| 卸完工具面**逐字复原** | 只看 `installed` 表会漏掉工具面上的残留 |
| 升级改工具名 → 拒绝 | `identity_drift` 抛出即可，这条不容易错 |
| `check` 抛错 → 版本与工具面都不变 | 只回滚 `installed` 表、不回滚 surface 是典型 bug |
| **装配失败 → 真的回到旧版本** | 同上；且要验**旧版本能继续调**，不是"能查到名字" |
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hos_ares_core.plugins.lifecycle import Lifecycle, PluginError, PluginSpec  # noqa: E402
from hos_ares_core.tools.surface import Impact, build_surface  # noqa: E402


def _spec(name: str = "p", version: str = "1.0.0", tool: str = "plugin.p.scan",
          impact: Impact = Impact.READ) -> PluginSpec:
    from hos_ares_core.plugins.lifecycle import PluginSpec as Spec

    return Spec(name=name, version=version, mount="act", tool_name=tool, impact=impact)


def _catalog_names(lc: Lifecycle) -> list[str]:
    return [row["name"] for row in lc.surface.catalog()]


# ── 1：装 ────────────────────────────────────────────────────
def test_install_exposes_the_tool() -> None:
    surface = build_surface()
    before = _catalog_names(Lifecycle(surface=surface))
    lc = Lifecycle(surface=surface)
    lc.install(_spec())
    assert "plugin.p.scan" in _catalog_names(lc)
    assert "plugin.p.scan" not in before


def test_install_twice_is_refused() -> None:
    lc = Lifecycle(surface=build_surface())
    lc.install(_spec())
    with pytest.raises(PluginError, match="already_installed"):
        lc.install(_spec())


# ── 2：卸（逐字复原）────────────────────────────────────────
def test_uninstall_restores_the_surface_exactly() -> None:
    surface = build_surface()
    before = lc_before = None
    lc = Lifecycle(surface=surface)
    before = lc.surface.catalog()
    lc.install(_spec())
    lc.uninstall("p")
    after = lc.surface.catalog()
    assert after == before, f"卸载没逐字复原：多出 {set(after) - set(before)}"


def test_uninstall_clears_knowledge_and_config() -> None:
    from hos_ares_core.plugins.lifecycle import PluginSpec as Spec

    lc = Lifecycle(surface=build_surface())
    lc.install(Spec(name="p", version="1.0.0", mount="act", tool_name="plugin.p.scan",
                    impact=Impact.READ, knowledge=("k1",), config={"a": 1}))
    assert lc.knowledge and lc.config
    lc.uninstall("p")
    assert not lc.knowledge and not lc.config, "卸载留了知识或配置"


# ── 3：升级不许改身份 ───────────────────────────────────────
def test_upgrade_may_not_rename_the_tool() -> None:
    lc = Lifecycle(surface=build_surface())
    lc.install(_spec())
    with pytest.raises(PluginError, match="identity_drift"):
        lc.upgrade(_spec(version="2.0.0", tool="plugin.p.renamed"))


def test_upgrade_may_not_change_the_impact_level() -> None:
    lc = Lifecycle(surface=build_surface())
    lc.install(_spec())
    with pytest.raises(PluginError, match="identity_drift"):
        lc.upgrade(_spec(version="2.0.0", impact=Impact.EXEC))


# ── 4：check 抛错 → 版本与现场都不变 ────────────────────────
def test_failed_check_leaves_version_and_surface_untouched() -> None:
    lc = Lifecycle(surface=build_surface())
    lc.install(_spec(version="1.0.0"))
    snapshot = lc.surface.catalog()

    def _reject(_spec: Any) -> None:
        raise RuntimeError("preflight says no")

    with pytest.raises(RuntimeError):
        lc.upgrade(_spec(version="2.0.0"), check=_reject)

    assert lc.installed["p"].version == "1.0.0", "check 失败却升了版本"
    assert lc.surface.catalog() == snapshot, "check 失败却动了工具面"


# ── 5：装配失败 → **真的**回到旧版本（能继续调）─────────────
def test_mount_failure_rolls_back_to_a_working_old_version() -> None:
    """装配失败之后，旧版本必须**还能被调用**，不只是"名字还在表里"。

    这条是本批最实质的一条：只恢复 `installed` 字典、不重新挂回 surface，
    会让插件"看起来还在、实际调不动"——那比直接报错更糟。
    """
    calls: list[str] = []
    surface = build_surface()

    def old_handler(_args: Any) -> dict[str, Any]:
        calls.append("v1")
        return {"v": 1}

    lc = Lifecycle(surface=surface)
    lc.install(_spec(version="1.0.0"), handler=old_handler)
    assert lc.surface.invoke("plugin.p.scan", {})["v"] == 1

    # 让**新版本**装配失败一次（模拟"名字被别处占了"）。
    # 只拦一次：回滚时要把旧版本重新挂回去，那一次必须放行——
    # 否则验的就不是产品的回滚，而是我替身写得过宽。
    original_register = surface.register
    state = {"blocked": False}

    def _fail_once(spec: Any) -> None:
        if not state["blocked"] and getattr(spec, "name", "") == "plugin.p.scan":
            state["blocked"] = True
            raise RuntimeError("surface refused")
        original_register(spec)

    surface.register = _fail_once  # type: ignore[method-assign]
    try:
        with pytest.raises(PluginError, match="upgrade_failed"):
            lc.upgrade(_spec(version="2.0.0"), handler=lambda a: {"v": 2})
    finally:
        surface.register = original_register  # type: ignore[method-assign]

    assert lc.installed["p"].version == "1.0.0", lc.installed["p"].version
    # **关键**：旧版本还能调
    assert lc.surface.invoke("plugin.p.scan", {})["v"] == 1, "回滚后旧版本调不动"
    assert calls and calls[-1] == "v1", calls
