"""CLI 共用小工具：Windows 控制台默认编码下，中文会乱码。

一个地方做对，六个命令共用——不在每个 cmd_*.py 里各写一份 `reconfigure`。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, cast


def _reconfigure(stream: Any, **kwargs: Any) -> None:
    """有 reconfigure 就切，没有就跳过。

    测试用 ``io.StringIO`` 替换 sys.stdin —— 它没有 reconfigure，
    直接调用会 AttributeError，把整条命令打死。
    """
    fn = getattr(stream, "reconfigure", None)
    if callable(fn):
        fn(**kwargs)


def setup_stdout() -> None:
    """把 stdout 切成 utf-8（Windows 控制台默认可能是 gbk）。"""
    _reconfigure(sys.stdout, encoding="utf-8", errors="replace")


def setup_stdio() -> None:
    """stdout **和** stdin 都切 utf-8。

    只切 stdout 不够：管道/重定向进来的中文会按 locale（Windows 上是 GBK）解码，
    于是「往 x.txt 写 hello」里的「写」变成乱码，只有 ASCII 路径能认出来——
    界面会把每句话都当成"读文件"（B77 实测踩到）。读交互输入的命令必须用这个。
    """
    setup_stdout()
    # utf-8-sig：管道/重定向过来的流常带 BOM。用 utf-8 解码会把 BOM 留在第一个
    # token 里（"read" 变成 "\ufeffread"），指令模式当场全废；没有 BOM 时两者等价。
    _reconfigure(sys.stdin, encoding="utf-8-sig", errors="replace")


def read_text_arg(path: str, *, what: str = "文件") -> str | None:
    """读一个**用户在命令行给出的**文本文件；读不到就一句人话并返回 None。

    调用方拿到 None 就 `return 2`。抛 `OSError` 出去换来的是一屏 traceback（B235）：
    客户第一次失败几乎总是路径打错，他需要看见的是"哪个文件、错在哪"。
    """
    # Windows 上读目录抛的是 PermissionError（不是 IsADirectoryError），所以先看形态。
    if Path(path).is_dir():
        print(f"{what}是个目录，不是文件：{path}", file=sys.stderr)
        return None
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"{what}不存在：{path}", file=sys.stderr)
    except IsADirectoryError:
        print(f"{what}是个目录，不是文件：{path}", file=sys.stderr)
    except UnicodeDecodeError:
        print(f"{what}不是 UTF-8 文本：{path}（先转成 UTF-8）", file=sys.stderr)
    except OSError as exc:
        print(f"{what}读不出来：{path}（{exc.strerror or exc}）", file=sys.stderr)
    return None


__all__ = ["read_text_arg", "setup_stdio", "setup_stdout"]
