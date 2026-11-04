#!/usr/bin/env python3
"""薄壳：控制台启动已收进 `veripen_cli.cmd_console`（B37）。

保留此文件只为兼容旧命令：
    python scripts/serve_console.py --port 8099
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from veripen_cli.cmd_console import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
