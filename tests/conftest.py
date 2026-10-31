"""pytest 引导：把仓库根加入导入路径（无需安装即可跑测试）。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "tests", ROOT / "tests" / "unit"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
