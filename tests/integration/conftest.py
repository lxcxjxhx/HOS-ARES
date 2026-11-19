"""集成段的浏览器进程哨兵（B504 / B505）。

问题：跑完整个集成序列后，`msedge` 会累积到 25–49 个并一直不退；
但**单跑任何一个浏览器用例（`test_console_render`）新增 0**（B460 实测）。
所以它不是"每个用例都漏"，而是"某事在整段序列里发生"。

B505 修正了一次观测错误：第一版只比较**进程数**（`after > before`），
而遗留进程会让 baseline 漂移——上一个用例留下 9 个、这一个又起 9 个，
计数法看到的仍是"没变化"。现在改用 **PID 集合差**：只报「本用例结束后新出现且仍活着」的那些。

    HOS_ARES_WATCH_BROWSERS=1 pytest tests/integration -q 2>&1 | grep BROWSER-WATCH
    # BROWSER-WATCH tests/integration/test_xxx.py::test_yyy: 留下 9 个 PID [22004, 14464, ...]

默认关闭（`_ENABLED` 为假时一个函数都不调），不拖慢正常跑。
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

_ENABLED = os.environ.get("HOS_ARES_WATCH_BROWSERS") == "1"
_PS = ("powershell", "-NoProfile", "-Command",
       "(Get-Process msedge -ErrorAction SilentlyContinue).Id -join ','")


def _browser_pids() -> set[int]:
    """当前 msedge 的 PID 集合。用集合不用计数——计数看不出「换了一批」。"""
    try:
        out = subprocess.run(_PS, capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    cleaned = (out or "").replace("\r", "").replace("\n", "")
    return {int(x) for x in cleaned.split(",") if x.strip().isdigit()}


@pytest.fixture(autouse=True)
def _browser_watch(request: pytest.FixtureRequest):
    """记录每个用例**结束后新出现且仍活着**的 msedge PID。"""
    if not _ENABLED:
        yield
        return
    before = _browser_pids()
    yield
    leaked = _browser_pids() - before
    if leaked:
        print(f"\nBROWSER-WATCH {request.node.nodeid}: 留下 {len(leaked)} 个 PID {sorted(leaked)[:6]}",
              file=sys.stderr, flush=True)
