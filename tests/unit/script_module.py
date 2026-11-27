"""把仓库里的脚本文件当模块加载（B586）。

## 为什么需要这个

同一段样板在 `tests/` 里出现了 **24 次**：

```python
spec = importlib.util.spec_from_file_location(name, path)
assert spec and spec.loader          # ← 常常写在使用之后
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
```

`spec_from_file_location` 的返回类型是 `ModuleSpec | None`。**断言写在使用之后等于没写**
——静态上每处解引用都在裸操作一个可能为 `None` 的值。

这个缺陷被复制到了三处测试里，各修一遍（B580 `test_tour_doc.py`、B582
`test_demo_manifest.py`、B585 `test_tour_build.py`）。**三次单独修是不对的，
应该让下一次复制不到错的样板。**

## 用法

```python
from tests.unit.script_module import load_script_module

mod = load_script_module("build_tour", ROOT / "scripts" / "demo" / "build_tour.py")
pages, scenes = mod._counts()
```

返回 `Any`：被加载的脚本没有类型存根，属性只能靠运行时。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any


def load_script_module(name: str, path: Path | str) -> Any:
    """把 ``path`` 处的 `.py` 当模块加载并返回。

    **`assert` 在解引用之前**——这是这个函数存在的唯一理由：
    让调用方不必重复那四行，也就不会再重复写错顺序。

    参数：
        name: 注册到 `sys.modules` 的名字（同一文件多次加载要换名字，否则拿到旧模块）
        path: 脚本路径（`Path` 或字符串）

    返回 `Any` 而不是 `ModuleType`：调用方接下来会访问脚本里的私有函数/常量，
    那些没有类型信息。标 `ModuleType` 只会在每个属性访问处再报一次错。
    """
    target = Path(path)
    # 文件先查一遍：`spec_from_file_location` 对不存在的文件返回 `None`，
    # 而 `exec_module` 会抛 `FileNotFoundError` —— 两种错误类型不一致，
    # 对调用方是误导（B586 实测：判据期望 ImportError，实际拿到 FileNotFoundError）。
    if not target.is_file():
        raise ImportError(f"加载不了脚本模块（文件不存在）：{target}")
    spec = importlib.util.spec_from_file_location(name, target)
    # 顺序关键：先断言，再使用
    if spec is None or spec.loader is None:
        raise ImportError(f"加载不了脚本模块（无 loader）：{target}")
    mod: ModuleType = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except FileNotFoundError as exc:          # 竞态：查完到执行之间被删
        raise ImportError(f"加载不了脚本模块：{target}") from exc
    return mod


__all__ = ["load_script_module"]
