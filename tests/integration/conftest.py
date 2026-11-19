"""集成段的浏览器进程哨兵（B504）。

问题：跑完整个集成序列后，`msedge` 会累积到 25–49 个并一直不退；
但**单跑任何一个浏览器用例（`test_console_render`）新增 0**（B460 实测）。
所以它不是"每个用例都漏"，而是"某事在整段序列里发生"。

这段仅在看门狗开着时工作（`HOS_ARES_WATCH_BROWSERS=1`），开销是一次进程枚举/用例：

    HOS_ARES_WATCH_BROWSERS=1 pytest tests/integration -q 2>&1 | grep BROWSER-WATCH

输出里每个**跳增**的用例会被记一行，直接指出是谁留下了进程。
关掉时（默认）一个函数都不调，不影响正常运行。
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

_ENABLED = os.environ.get("HOS_ARES_WATCH_BROWSERS") == "1"
_PS = ("powershell", "-NoProfile", "-Command",
       "(Get-Process msedge -ErrorAction SilentlyContinue | Measure-Object).Count")


def _count_browsers() -> int:
    try:
        out = subprocess.run(_PS, capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return -1
    raw = (out or "").strip()
    return int(raw) if raw.isdigit() else -1


@pytest.fixture(autouse=True)
def _browser_watch(request: pytest.FixtureRequest):
    """记录每个用例前后 msedge 进程数；只在跳增时打印。"""
    if not _ENABLED:
        yield
        return
    before = _count_browsers()
    yield
    after = _count_browsers()
    if before >= 0 and after > before:
        print(f"\nBROWSER-WATCH {request.node.nodeid}: {before} -> {after} (+{after - before})",
              file=sys.stderr, flush=True)
