"""把 `chat` 的执行路径接到**工具面**上（B545）。

问题（B47 声称"工具面统一"，实际没有）：

| 层 | 工具名 | 执行在哪 |
|---|---|---|
| 工具面（`build_surface`） | `fs.read` / `fs.write` / `fs.edit` / `shell.run` / `browser.render` | `surface.invoke()`，**带审批与审计** |
| `chat` 实际执行 | `read` / `write` / `edit` / `patch` / `sh` | `_chat_executor()` 直接跑，**绕过审批与审计** |

两套名字没有交集：模型被允许的 12 个工具（`SCHEMAS ∪ surface_names()`）里
**没有一个**是 chat 能执行的；而 chat 能执行的 5 个，白名单里一个都没有。

这一层补的是**名字到工具面的映射**，不是第二套实现：

- `read` → `fs.read`、`write` → `fs.write`、`edit` → `fs.edit`（参数名逐字对应）；
- `sh` → `shell.run`（复用同一个执行器，审批与审计走工具面）；
- 工具面里没有的（`patch` 多文件、`status`/`run`/`platform` 这些命令式步），
  继续走 chat 自己的实现——它们**不是文件工具**，硬塞进工具面只会让两边都变形。

映射表写在这里而不是散在 `if/elif` 里：将来加一个工具，改一处。
"""

from __future__ import annotations

from typing import Any, Mapping

#: `chat` 的工具名 → 工具面里的规范名。只列**语义一一对应**的。
SURFACE_ALIASES: dict[str, str] = {
    "read": "fs.read",
    "write": "fs.write",
    "edit": "fs.edit",
    "sh": "shell.run",
}

#: 参数名映射：chat 用 `path`，工具面用 `rel`。逐条写清，不做"去掉前缀"之类的猜测。
ARG_ALIASES: dict[str, dict[str, str]] = {
    "read": {"path": "rel"},
    "write": {"path": "rel"},
    "edit": {"path": "rel"},
    "sh": {"cmd": "script"},
}


class NotOnSurface(ValueError):
    """这个工具名不在工具面里：调用方该走自己的实现，不是报错。"""


def surface_name(tool: str) -> str:
    name = SURFACE_ALIASES.get(tool)
    if name is None:
        raise NotOnSurface(tool)
    return name


def surface_args(tool: str, args: Mapping[str, Any]) -> dict[str, Any]:
    """把 chat 的参数名翻成工具面的参数名。**不认识的键原样带过去**——
    schema 会拒掉它，那是"参数不对"该有的报错，不是我在这里悄悄丢掉。"""
    mapping = ARG_ALIASES.get(tool, {})
    return {mapping.get(k, k): v for k, v in args.items()}


def describe() -> dict[str, str]:
    """给 `doctor` / 文档用的一行说明表。"""
    return dict(SURFACE_ALIASES)


__all__ = [
    "ARG_ALIASES",
    "NotOnSurface",
    "SURFACE_ALIASES",
    "describe",
    "surface_args",
    "surface_name",
]
