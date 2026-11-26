"""B545 · chat 接上工具面：审批门与审计不再被绕过。

B47 声称"工具面统一"，实际是两套：

| 层 | 工具名 | 执行在哪 | 审批门 | 审计 |
|---|---|---|---|---|
| 工具面 | `fs.read` / `fs.write` / `fs.edit` / `shell.run` / `browser.render` | `surface.invoke()` | ✅ | ✅ |
| `chat` 实际 | `read` / `write` / `edit` / `patch` / `sh` | 直接跑 | ❌ | ❌ |

两套名字**没有交集**：模型被允许的 12 个工具里没有一个 chat 能执行；
chat 能执行的 5 个，白名单里一个都没有。

这一批补的是映射（`surface_bridge`），不是第二套实现。
判决性的一条：**exec 类不给审批就必须拒**，且这条拒绝要出现在审计里。
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hos_ares_core.tools.surface import Impact, ToolSpec, ToolSurface, build_surface  # noqa: E402
from hos_ares_core.workbench import surface_bridge  # noqa: E402
from hos_ares_core.workbench.fs import Workbench  # noqa: E402


# ── 1：映射表本身 ────────────────────────────────────────────
def test_every_alias_points_to_a_real_surface_tool() -> None:
    """别名不许指到不存在的工具：那会变成"看起来接上了，其实调不通"。"""
    names = {row["name"] for row in build_surface().catalog()}
    for chat_name, surface_name in surface_bridge.SURFACE_ALIASES.items():
        assert surface_name in names, f"{chat_name} → {surface_name} 不在工具面里"


def test_argument_names_are_translated_not_dropped() -> None:
    """`path` → `rel` 必须翻；**不认识的键原样带过去**（让 schema 去拒，不在这里丢）。"""
    assert surface_bridge.surface_args("read", {"path": "a.txt"}) == {"rel": "a.txt"}
    assert surface_bridge.surface_args("sh", {"cmd": "echo x"}) == {"script": "echo x"}
    extra = surface_bridge.surface_args("read", {"path": "a.txt", "bogus": 1})
    assert extra["bogus"] == 1, "不许悄悄丢掉不认识的参数"


def test_tools_outside_the_surface_are_reported_not_guessed() -> None:
    for name in ("patch", "status", "run", "platform"):
        with pytest.raises(surface_bridge.NotOnSurface):
            surface_bridge.surface_name(name)


# ── 2：审批门（这批的核心价值）────────────────────────────────
def _surface(tmp_path: Path) -> ToolSurface:
    def _shell(args: Mapping[str, object]) -> Mapping[str, object]:
        return {"exit_code": 0, "stdout": "ran", "stderr": ""}

    return build_surface(workbench=Workbench(str(tmp_path)), shell_runner=_shell)


def test_exec_without_approval_is_refused_and_audited(tmp_path: Path) -> None:
    """没审批就不许跑——而且**拒绝本身要留痕**，不是静默失败。"""
    from hos_ares_core.tools.surface import SurfaceError

    surface = _surface(tmp_path)
    with pytest.raises(SurfaceError, match="approval_required"):
        surface.invoke("shell.run", {"script": "echo x"}, approved=False)
    audit = list(surface.audit())
    assert audit and audit[-1]["result"] == "approval_required", audit
    assert audit[-1]["allowed_by"] is None, audit[-1]


def test_exec_with_approval_runs_and_is_audited(tmp_path: Path) -> None:
    surface = _surface(tmp_path)
    result = surface.invoke("shell.run", {"script": "echo x"}, approved=True)
    assert result["ok"] is True
    assert result["stdout"] == "ran"
    audit = list(surface.audit())
    assert audit[-1]["allowed_by"] == "approval", audit[-1]


def test_read_and_write_do_not_need_exec_approval_but_are_audited(tmp_path: Path) -> None:
    surface = _surface(tmp_path)
    wrote = surface.invoke("fs.write", {"rel": "t.txt", "content": "hi", "overwrite": True})
    read = surface.invoke("fs.read", {"rel": "t.txt"})
    assert wrote["written"] == 2 and read["text"] == "hi"
    results = [(r["tool"], r["allowed_by"]) for r in surface.audit()]
    assert ("fs.write", "auto") in results and ("fs.read", "auto") in results


def test_duplicate_registration_is_refused() -> None:
    """一个名字只能有一份实现：否则"哪份在跑"取决于注册顺序。"""
    from hos_ares_core.tools.surface import SurfaceError

    surface = ToolSurface()
    surface.register(ToolSpec("x", Impact.READ, "probe", None, lambda a: {}))
    with pytest.raises(SurfaceError, match="duplicate_tool"):
        surface.register(ToolSpec("x", Impact.READ, "probe", None, lambda a: {}))


# ── 3：chat 真的走这条路径 ────────────────────────────────────
def test_chat_routes_read_write_edit_sh_through_the_surface() -> None:
    """四件文件/执行工具都要有映射；缺一个就退回"绕过审批"的老路。"""
    bridged = set(surface_bridge.SURFACE_ALIASES)
    assert bridged == {"read", "write", "edit", "sh"}, bridged


def test_chat_exec_step_prefers_the_surface(tmp_path: Path) -> None:
    """`_exec_step` 对 `read` 必须走工具面（返回里有"行/b"这类工具面措辞）。"""
    from hos_ares_cli import cmd_chat
    from hos_ares_core.agent.intent import Step

    (tmp_path / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    wb = Workbench(str(tmp_path))
    ok, text = cmd_chat._exec_step(
        Step("read", {"path": "a.txt"}, "读"), wb=wb, root=tmp_path
    )
    assert ok is True, text
    assert "2 行" in text, text


def test_danger_words_still_blocked_before_the_surface(tmp_path: Path) -> None:
    """危险词闸门在工具面**之前**：接上工具面不许把这个闸门顶掉。"""
    from hos_ares_cli import cmd_chat
    from hos_ares_core.agent.intent import Step

    wb = Workbench(str(tmp_path))
    ok, text = cmd_chat._exec_step(
        Step("sh", {"cmd": "rm -rf /"}, "执行"), wb=wb, root=tmp_path, allow_dangerous=False
    )
    assert ok is False
    assert "GATE-DANGER" in text, text
