"""B47 工具面统一：一个注册表，一套准入，一本审计。

之前是两套：
- agent 的 7 个工具在 ``tools/registry.py``（模型看得见，走 validate + dispatch）；
- fs / shell 在 ``workbench/``（agent 看不见，绕开预算与审计）。

现在两套合并成 ``ToolSurface``：**名称唯一、schema 唯一、impact 唯一、审计唯一**。
执行仍留在各自实现里（不搬家），surface 只做"贴标签 + 门 + 记账"。

三条边界：
1. ``impact=exec`` 必须显式 ``approved=True`` 才能执行（审批门在 B54，这里只做闸）；
2. 未配置的实现**不报错、返回 blocked**——"没接线"和"执行失败"是两件事；
3. 只产事实与产物，**永不产 verdict**（`act.*` 的类型隔离沿用 `H1`）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Protocol, Sequence

from pydantic import BaseModel, ConfigDict


class Impact(str, Enum):
    READ = "read"
    WRITE = "write"
    EXEC = "exec"


class SurfaceError(RuntimeError):
    """注册或调用失败的统一异常（code 供上层归类，不用字符串匹配）。"""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class WorkbenchLike(Protocol):
    def read(self, rel: str) -> Any: ...
    def write(self, rel: str, content: str, *, overwrite: bool = False) -> Any: ...
    def edit(self, rel: str, *, old: str, new: str, expect_count: int = 1) -> Any: ...


Handler = Callable[[Mapping[str, Any]], Mapping[str, Any]]


def _strict() -> ConfigDict:
    return ConfigDict(extra="forbid", frozen=True)


class _Call(BaseModel):
    model_config = _strict()


class FsReadCall(_Call):
    #: 工作区内**相对**路径。字段名用 ``rel`` 而不是 ``path``：
    #: 地址闸门把 ``path`` 视为路由片段，工作台路径不是地址（B47 实测撞到）。
    rel: str


class FsWriteCall(_Call):
    rel: str
    content: str
    overwrite: bool = False


class FsEditCall(_Call):
    rel: str
    old: str
    new: str
    expect_count: int = 1


class ShellRunCall(_Call):
    script: str
    interpreter: str = "python"
    cwd: str = "."
    timeout_s: int = 60


class BrowserRenderCall(_Call):
    route_ref: str
    wait: str = "domcontentloaded"


#: 扩展工具（agent 原 7 个之外的）
EXTRA_SCHEMAS: dict[str, type[_Call]] = {
    "fs.read": FsReadCall,
    "fs.write": FsWriteCall,
    "fs.edit": FsEditCall,
    "shell.run": ShellRunCall,
    "browser.render": BrowserRenderCall,
}

#: agent 原 7 个工具的 impact（执行仍走 ``tools.registry.dispatch``）
AGENT_IMPACT: Mapping[str, Impact] = {
    "list_assets": Impact.READ,
    "list_routes": Impact.READ,
    "run_oracle": Impact.READ,
    "rediscover_routes": Impact.READ,
    "record_note": Impact.WRITE,
    "request_approval": Impact.WRITE,
    "finish": Impact.READ,
}

EXTRA_IMPACT: Mapping[str, Impact] = {
    "fs.read": Impact.READ,
    "fs.write": Impact.WRITE,
    "fs.edit": Impact.WRITE,
    "shell.run": Impact.EXEC,
    "browser.render": Impact.EXEC,
}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    impact: Impact
    description: str
    schema: type[BaseModel] | None = None
    handler: Handler | None = None


@dataclass
class ToolSurface:
    """唯一工具面。注册顺序无关（catalog 排序），重复注册直接失败。"""

    _specs: dict[str, ToolSpec] = field(default_factory=dict)
    _audit: list[dict[str, Any]] = field(default_factory=list)

    # ── 注册 ────────────────────────────────────────────────────
    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._specs:
            raise SurfaceError("duplicate_tool", f"{spec.name} 已注册（一个动作只能有一个实现）")
        self._specs[spec.name] = spec

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._specs))

    def catalog(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            {"name": s.name, "impact": s.impact.value,
             "required": sorted(s.schema.model_fields) if s.schema else [],
             "implemented": s.handler is not None}
            for s in (self._specs[n] for n in self.names())
        )

    # ── 调用 ────────────────────────────────────────────────────
    def invoke(self, name: str, args: Mapping[str, Any], *, approved: bool = False) -> dict[str, Any]:
        spec = self._specs.get(name)
        if spec is None:
            raise SurfaceError("unknown_tool", name)
        if spec.impact is Impact.EXEC and not approved:
            self._audit.append({"tool": name, "impact": spec.impact.value,
                                "allowed_by": None, "result": "approval_required"})
            raise SurfaceError("approval_required", f"{name} 属于 exec，必须显式审批")
        if spec.schema is not None:
            args = dict(spec.schema(**dict(args)).model_dump())
        if spec.handler is None:
            self._audit.append({"tool": name, "impact": spec.impact.value,
                                "allowed_by": "approval" if approved else "auto",
                                "result": "not_configured"})
            return {"ok": False, "outcome": "blocked", "reason": "not_configured"}
        try:
            data = dict(spec.handler(args))
        except Exception as exc:  # 实现层异常归一，不向上冒成崩溃
            self._audit.append({"tool": name, "impact": spec.impact.value,
                                "allowed_by": "approval" if approved else "auto",
                                "result": "error"})
            return {"ok": False, "outcome": "error", "reason": str(exc)[:200]}
        self._audit.append({"tool": name, "impact": spec.impact.value,
                            "allowed_by": "approval" if approved else "auto", "result": "ok"})
        return {"ok": True, "outcome": "ok", **data}

    def audit(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._audit)


def build_surface(
    *,
    workbench: WorkbenchLike | None = None,
    shell_runner: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    browser_renderer: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
) -> ToolSurface:
    """装配工具面。缺实现 = 该工具 ``implemented=False``，调用返回 blocked。"""
    surface = ToolSurface()

    for name, impact in AGENT_IMPACT.items():
        surface.register(ToolSpec(name, impact, f"agent tool {name}"))

    def _fs_read(args: Mapping[str, Any]) -> Mapping[str, Any]:
        if workbench is None:
            raise SurfaceError("not_configured", "workbench 未注入")
        result = workbench.read(str(args["rel"]))
        return {"path": str(args["rel"]), "text": str(getattr(result, "text", "")),
                "truncated": bool(getattr(result, "truncated", False))}

    def _fs_write(args: Mapping[str, Any]) -> Mapping[str, Any]:
        if workbench is None:
            raise SurfaceError("not_configured", "workbench 未注入")
        result = workbench.write(str(args["rel"]), str(args["content"]),
                                 overwrite=bool(args.get("overwrite", False)))
        return {"path": str(args["rel"]), "written": int(getattr(result, "bytes_written", 0))}

    def _fs_edit(args: Mapping[str, Any]) -> Mapping[str, Any]:
        if workbench is None:
            raise SurfaceError("not_configured", "workbench 未注入")
        result = workbench.edit(str(args["rel"]), old=str(args["old"]), new=str(args["new"]),
                                expect_count=int(args.get("expect_count", 1)))
        return {"path": str(args["rel"]), "replacements": int(getattr(result, "replacements", 0))}

    surface.register(ToolSpec("fs.read", Impact.READ, "读工作区文件（作用域内）",
                              FsReadCall, _fs_read if workbench else None))
    surface.register(ToolSpec("fs.write", Impact.WRITE, "写工作区文件",
                              FsWriteCall, _fs_write if workbench else None))
    surface.register(ToolSpec("fs.edit", Impact.WRITE, "按字面量替换（次数必须匹配）",
                              FsEditCall, _fs_edit if workbench else None))
    surface.register(ToolSpec("shell.run", Impact.EXEC, "跑脚本（五道闸在 shell 层）",
                              ShellRunCall, shell_runner))
    surface.register(ToolSpec("browser.render", Impact.EXEC, "渲染目标页并取样 DOM",
                              BrowserRenderCall, browser_renderer))
    return surface


def surface_names() -> frozenset[str]:
    """给 agent 白名单用的名称集合（不装配实现，只问名字）。"""
    return frozenset(AGENT_IMPACT) | frozenset(EXTRA_IMPACT)


__all__ = [
    "AGENT_IMPACT",
    "EXTRA_IMPACT",
    "EXTRA_SCHEMAS",
    "Impact",
    "SurfaceError",
    "ToolSpec",
    "ToolSurface",
    "build_surface",
    "surface_names",
]
