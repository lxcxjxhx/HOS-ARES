"""B265 · 演示脚本的旁白时间线必须单调。

改前实跑（真跑 `scripts/demo.py`）：

```
[1:30] 证据：4 个 capsule，链完整=True，head=4b0640801e8e · tip=cd7d91848f08
[1:10] 线索账本：4 条全部闭环；死信 1 条（单列，演示重试耗尽路径）
[1:30] 报告三视图：4 条，零地址命中=0，manifest=cced194fd9c3
```

`1:30 → 1:10 → 1:30`：在终端里读起来就是"时间倒流"。这些方括号是**旁白时间线**
（成片的讲述节奏），不是真实耗时——所以它必须至少单调不减，否则观众第一眼就出戏。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_LABEL = re.compile(r"^\[(\d+):(\d\d)\] ", re.MULTILINE)


def _run_demo() -> str:
    # 环境不能整份替换（B364）：以前只传 PATH + PYTHONIOENCODING，孩子进程里
    # ① `yaml` 装在 user site，APPDATA 没了就 import 不到；
    # ② `Path.home()` 直接 RuntimeError（没有 USERPROFILE）。
    # 这不是"干净环境跑一遍"，是"缺了半台机器的环境跑一遍"。
    proc = subprocess.run(
        [sys.executable, "scripts/demo.py"],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    return proc.stdout


def test_demo_timeline_never_goes_backwards() -> None:
    stamps = [(int(m), int(s)) for m, s in _LABEL.findall(_run_demo())]
    assert len(stamps) >= 8, stamps
    back = [(a, b) for a, b in zip(stamps, stamps[1:]) if b < a]
    assert not back, f"旁白时间线倒流：{back}（全部：{stamps}）"


def test_demo_timeline_starts_at_zero_and_ends_inside_the_promised_minutes() -> None:
    stamps = [(int(m), int(s)) for m, s in _LABEL.findall(_run_demo())]
    assert stamps[0] == (0, 0), stamps[0]
    assert stamps[-1] <= (6, 0), f"成片承诺 5 分钟左右，最后一步却在 {stamps[-1]}"
