"""pytest 引导：把仓库根加入导入路径（无需安装即可跑测试）。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "tests", ROOT / "tests" / "unit"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


# **为什么在这里再导出一次 `Lab`（B576）**：
# `pyproject.toml` 的 `mypy_path = ["tests", "tests/unit"]` 让 `import conftest`
# 先解析到**本文件**，而不是 `tests/unit/conftest.py`。于是测试里的
# `from conftest import Lab` 报 `Module "conftest" has no attribute "Lab"`。
#
# 运行时：`tests/unit` 已在 sys.path 上（上面那个循环加的），可以直接导入。
# 静态上：这行 `from unit.conftest import ...` 让 mypy 能看到符号。
if str(ROOT / "tests" / "unit") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests" / "unit"))

from unit.conftest import Lab, both_modes, hardened, vulnerable  # noqa: E402

__all__ = ["Lab", "both_modes", "hardened", "vulnerable"]
