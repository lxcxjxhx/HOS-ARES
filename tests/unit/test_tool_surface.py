"""B47 工具面统一（`H07` B47 / `H02 §4`）。

四条判据：
A 注册去重（同名两个实现 = 启动失败）；
B agent 能调到 fs.read / shell.run（同一张清单，同一入口）；
C impact 分级可见，`exec` 必须显式审批；
D 旧的两条注册路径合并——模型白名单与 dispatch 都从 surface 派生。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from veripen_core.agent.client import ALLOWED_TOOLS
from veripen_core.tools import registry as registry_mod
from veripen_core.tools.registry import ToolCall, dispatch, tool_schemas
from veripen_core.tools.surface import (
    EXTRA_SCHEMAS,
    Impact,
    SurfaceError,
    ToolSpec,
    build_surface,
    surface_names,
)
from veripen_core.workbench.fs import Workbench


# ── A 注册去重 ─────────────────────────────────────────────────
def test_duplicate_registration_fails_at_startup() -> None:
    surface = build_surface()
    with pytest.raises(SurfaceError) as err:
        surface.register(ToolSpec("fs.read", Impact.READ, "重复实现"))
    assert err.value.code == "duplicate_tool"


def test_catalog_is_sorted_and_shows_impact() -> None:
    surface = build_surface()
    catalog = surface.catalog()
    names = [row["name"] for row in catalog]
    assert names == sorted(names)
    by_name = {row["name"]: row for row in catalog}
    assert by_name["shell.run"]["impact"] == "exec"
    assert by_name["fs.read"]["impact"] == "read"
    assert by_name["record_note"]["impact"] == "write"
    assert by_name["shell.run"]["implemented"] is False, "未注入实现必须诚实标 False"


# ── B 同一张清单、同一入口 ─────────────────────────────────────
def test_agent_whitelist_equals_surface_names() -> None:
    assert ALLOWED_TOOLS == surface_names(), "模型白名单必须由工具面派生（D）"
    assert set(tool_schemas()) == set(EXTRA_SCHEMAS) | set(registry_mod.SCHEMAS)


def test_fs_roundtrip_through_surface(tmp_path: Path) -> None:
    surface = build_surface(workbench=Workbench(tmp_path))
    written = surface.invoke("fs.write", {"rel": "notes/a.txt", "content": "hello"})
    assert written["ok"] and written["written"] == 5
    read = surface.invoke("fs.read", {"rel": "notes/a.txt"})
    assert read["text"] == "hello"
    edited = surface.invoke("fs.edit", {"rel": "notes/a.txt", "old": "hello", "new": "hi"})
    assert edited["replacements"] == 1


def test_workbench_uninjected_is_blocked_not_crashing() -> None:
    out = build_surface().invoke("fs.read", {"rel": "x"})
    assert out["ok"] is False and out["outcome"] == "blocked" and out["reason"] == "not_configured"


# ── C impact 分级与 exec 审批 ──────────────────────────────────
def test_exec_requires_explicit_approval() -> None:
    surface = build_surface()
    with pytest.raises(SurfaceError) as err:
        surface.invoke("shell.run", {"script": "print(1)"})
    assert err.value.code == "approval_required"
    assert surface.audit()[-1]["result"] == "approval_required"


def test_exec_runs_when_approved_and_configured() -> None:
    surface = build_surface(shell_runner=lambda args: {"exit_code": 0, "stdout": args["script"]})
    out = surface.invoke("shell.run", {"script": "print(1)"}, approved=True)
    assert out["ok"] and out["stdout"] == "print(1)"


def test_read_and_write_do_not_need_approval(tmp_path: Path) -> None:
    surface = build_surface(workbench=Workbench(tmp_path))
    assert surface.invoke("fs.read", {"rel": "a.txt"})["ok"] is False  # 不存在 → 归一错误
    assert surface.invoke("fs.write", {"rel": "a.txt", "content": "x"})["ok"]


def test_audit_records_every_call() -> None:
    surface = build_surface()
    surface.invoke("fs.read", {"rel": "x"})
    entry = surface.audit()[-1]
    assert entry["tool"] == "fs.read" and entry["impact"] == "read" and entry["result"] in {
        "ok", "not_configured", "error"}


def test_browser_render_without_renderer_is_blocked() -> None:
    out = build_surface().invoke("browser.render", {"route_ref": "route://contract/x"}, approved=True)
    assert out["outcome"] == "blocked" and out["reason"] == "not_configured"


# ── D 旧的两条路径合并 ────────────────────────────────────────
def test_dispatch_routes_extended_tools_through_surface(tmp_path: Path) -> None:
    surface = build_surface(workbench=Workbench(tmp_path))
    call = ToolCall(name="fs.write", args={"rel": "b.txt", "content": "ok"})
    result = dispatch(call, runtime=_Runtime(), surface=surface)  # type: ignore[arg-type]
    assert result.ok and (tmp_path / "b.txt").read_text(encoding="utf-8") == "ok"


def test_dispatch_without_surface_refuses_extended_tools() -> None:
    call = ToolCall(name="fs.read", args={"rel": "x"})
    result = dispatch(call, runtime=_Runtime())  # type: ignore[arg-type]
    assert result.ok is False and result.error_class == "unknown_tool"


class _Runtime:
    """dispatch 只走到扩展工具分支，agent 方法用不到——给个空壳。"""


# ── 类型隔离：工具面不产 verdict ──────────────────────────────
def test_surface_payload_has_no_verdict_field() -> None:
    surface = build_surface(shell_runner=lambda args: {"exit_code": 0})
    out = surface.invoke("shell.run", {"script": "x"}, approved=True)
    assert "verdict" not in out
