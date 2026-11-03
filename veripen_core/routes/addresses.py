"""地址形态识别（27 N3 / 35 §5）。

**这段知识属于路由层**：谁定义"什么是地址"，谁才能判定"哪里出现了地址"。
放在 ``actions/`` 里会让动作层自己维护一份地址语法，那是第二个事实源。

同时它解决一个真实矛盾：``route://`` 本身含 ``://``，
所以"含 ``://`` 就拒"会把合法的 ref 也拒掉。允许的白名单必须是显式的。
"""

from __future__ import annotations

import re

#: 允许出现在文本里的不透明 scheme（我们自己的引用 + 密钥引用）
ALLOWED_SCHEMES: tuple[str, ...] = ("route://", "vault://", "op://")

#: 地址指纹。**这里出现地址语法是本职**：本模块就是定义"什么是地址"的地方，
#: 换成别处查表反而多一个事实源。
_ADDRESS = re.compile(
    r"[a-zA-Z][a-zA-Z0-9+.\-]*://"
    r"|\b[0-9]{1,3}(\.[0-9]{1,3}){3}\b"
    r"|\b(localhost|0\.0\.0\.0)\b"  # noroute-ok: 定义环回字面量，正是要被识别的那批
    r"|(?<=[a-zA-Z]):[0-9]{2,5}(?![0-9])"
)


def scrub(text: str) -> str:
    """把允许的 scheme 换成占位，再交给地址指纹扫。**最长匹配优先**。"""
    out = text
    for literal in sorted(ALLOWED_SCHEMES, key=len, reverse=True):
        out = out.replace(literal, "<ok>")
    return out


def contains_address(text: str) -> bool:
    """``True`` 表示这段文本里出现了真实地址（允许的 scheme 除外）。"""
    return _ADDRESS.search(scrub(text)) is not None


def find_addresses(text: str) -> list[str]:
    """返回命中的地址片段；用于 lint 与报错信息。"""
    return [m.group(0) for m in _ADDRESS.finditer(scrub(text))]


__all__ = ["ALLOWED_SCHEMES", "contains_address", "find_addresses", "scrub"]
