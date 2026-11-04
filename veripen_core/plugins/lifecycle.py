"""B55 插件端到端：装 → 用 → 升级 → 卸。

四段里最容易做假的是"卸干净"：卸载后 `ToolSurface` 若不是**逐字相同**，就说明有残留。
所以判据不用"看起来对了"，而是 `catalog()` 的元组比较（顺序、impact、required、implemented 全在内）。

三条纪律：

1. **装前预演**：先告诉你它会拿到哪些 impact 级，再让你决定装；
2. **升级失败自动回滚**：新版本装配失败时，安装记录与工具面都停在旧版本；
3. **卸载即复原**：工具、知识、配置三样一起清，清单回到装前。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from ..tools.surface import Impact, ToolSpec, ToolSurface


class PluginError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class PluginSpec:
    """一个插件包能提供什么。``tool`` 为 None = 纯知识包。"""

    name: str
    version: str
    mount: str = "act"                     # knowledge / act / source / oracle
    tool_name: str | None = None
    impact: Impact = Impact.WRITE
    knowledge: tuple[str, ...] = ()
    config: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if self.mount not in ("knowledge", "act", "source", "oracle"):
            raise PluginError("bad_mount", self.mount)
        if self.mount == "knowledge" and self.tool_name:
            raise PluginError("knowledge_cannot_expose_tool", self.name)
        if self.mount != "knowledge" and not self.tool_name:
            raise PluginError("missing_tool", self.name)
        if self.tool_name and not self.tool_name.startswith(f"plugin.{self.name}."):
            raise PluginError("tool_namespace", f"{self.tool_name} 必须落在 plugin.{self.name}.")


@dataclass
class Lifecycle:
    surface: ToolSurface
    installed: dict[str, PluginSpec] = field(default_factory=dict)
    knowledge: dict[str, tuple[str, ...]] = field(default_factory=dict)
    config: dict[str, Mapping[str, Any]] = field(default_factory=dict)

    # ── 预演 ────────────────────────────────────────────────────
    def preview(self, spec: PluginSpec) -> dict[str, Any]:
        spec.validate()
        return {
            "name": spec.name, "version": spec.version, "mount": spec.mount,
            "impact": spec.impact.value,
            "needs_approval": spec.impact is Impact.EXEC,
            "knowledge_docs": len(spec.knowledge),
            "will_expose_tool": spec.tool_name,
            "already_installed": spec.name in self.installed,
        }

    # ── 装 ─────────────────────────────────────────────────────
    def install(self, spec: PluginSpec, *, handler: Callable[[Mapping[str, Any]], Mapping[str, Any]]
                | None = None) -> None:
        spec.validate()
        if spec.name in self.installed:
            raise PluginError("already_installed", spec.name)
        self._mount(spec, handler)
        self.installed[spec.name] = spec
        if spec.knowledge:
            self.knowledge[spec.name] = spec.knowledge
        if spec.config:
            self.config[spec.name] = dict(spec.config)

    def _mount(self, spec: PluginSpec,
               handler: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None) -> None:
        if spec.tool_name is None:
            return
        self.surface.register(ToolSpec(spec.tool_name, spec.impact,
                                       f"{spec.name}@{spec.version}",
                                       handler=handler))

    # ── 升级（失败回滚）─────────────────────────────────────────
    def upgrade(self, spec: PluginSpec, *,
                handler: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
                check: Callable[[PluginSpec], None] | None = None) -> None:
        current = self.installed.get(spec.name)
        if current is None:
            raise PluginError("not_installed", spec.name)
        spec.validate()
        if spec.tool_name != current.tool_name or spec.impact != current.impact:
            raise PluginError("identity_drift", "升级不许改工具名或 impact 级")
        if check is not None:
            check(spec)                      # 失败就抛，下面一行都不会执行
        previous = dict(self.installed)
        try:
            self._unmount(current)
            self._mount(spec, handler)
        except Exception as exc:             # 装配失败 → 回到旧版本
            self._unmount(spec)
            self._mount(current, None)
            self.installed = previous
            raise PluginError("upgrade_failed", str(exc)) from exc
        self.installed[spec.name] = spec

    # ── 卸 ─────────────────────────────────────────────────────
    def uninstall(self, name: str) -> None:
        current = self.installed.get(name)
        if current is None:
            raise PluginError("not_installed", name)
        self._unmount(current)
        self.installed.pop(name, None)
        self.knowledge.pop(name, None)
        self.config.pop(name, None)

    def _unmount(self, spec: PluginSpec) -> None:
        if spec.tool_name is None:
            return
        self.surface.unregister(spec.tool_name)


__all__ = ["Lifecycle", "PluginError", "PluginSpec"]
