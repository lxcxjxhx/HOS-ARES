"""CLI 共用小工具：Windows 控制台默认编码下，中文会乱码。

一个地方做对，六个命令共用——不在每个 cmd_*.py 里各写一份 `reconfigure`。
"""

from __future__ import annotations

import sys
from typing import Any, cast


def setup_stdout() -> None:
    """把 stdout 切成 utf-8（Windows 控制台默认可能是 gbk）。"""
    cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")


__all__ = ["setup_stdout"]
