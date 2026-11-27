"""控制台编码收口（B574）。

## 为什么需要它

Windows 的控制台默认是 GBK，脚本里带 `✅` / `✗` / 中文破折号时会抛
`UnicodeEncodeError: 'gbk' codec can't encode character ...`。

常规写法是 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`，但它有两个问题：

1. **类型上不成立**：`sys.stdout` 声明是 `TextIO`，没有 `reconfigure`——
   `mypy --strict` 报 `Item "TextIO" of "TextIO | Any" has no attribute "reconfigure"`；
2. **运行时不一定存在**：被重定向（管道、`StringIO`、某些 CI 采集器）时就没有这个方法。

于是同一个写法在 11 个脚本里各复制了一遍，每处都带一样的类型问题。

## 做法

`getattr` 取方法，取不到就跳过——**明说"可能没有"**，比 `# type: ignore` 诚实。
`

```python
from lint.console import utf8_stdout
utf8_stdout()          # 默认 stdout + stderr 都收口
```
"""

from __future__ import annotations

import sys
from typing import Any, TextIO


def _reconfigure(stream: Any, encoding: str, errors: str) -> None:
    """给一个流换编码；流不支持就静默跳过。"""
    method = getattr(stream, "reconfigure", None)
    if method is None:
        return
    try:
        method(encoding=encoding, errors=errors)
    except (ValueError, OSError):
        # 已经被写过一行的流不能再改编码（io.TextIOWrapper 的约束）；
        # 那种情况下保持原样，不因为"编码没改成"而让脚本失败。
        pass


def utf8_stdout(*, encoding: str = "utf-8", errors: str = "replace") -> None:
    """把 stdout / stderr 的编码收口成 UTF-8。"""
    _reconfigure(sys.stdout, encoding, errors)
    _reconfigure(sys.stderr, encoding, errors)


def utf8_stream(stream: TextIO, *, encoding: str = "utf-8", errors: str = "replace") -> None:
    """只收口指定的流（`utf8_stdout` 的内部实现，也供自测用）。"""
    _reconfigure(stream, encoding, errors)


__all__ = ["utf8_stream", "utf8_stdout"]
