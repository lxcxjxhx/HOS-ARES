"""B284 · 本地验一遍单文件 exe：打得出来、起得来、报的地址是真的。

CI 走 `.github/workflows/build-exe.yml`，本地没有对应入口——于是"打包声明漏了包"
（B283）这种事只能等 CI 才发现。`make exe-check` 就是本地那条命令 + 一次真启动。
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "out" / "dist" / "HOS-ARES.exe"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def main() -> int:
    if not EXE.exists():
        print(f"没有 {EXE}：先跑 make exe")
        return 2

    port = _free_port()
    proc = subprocess.Popen(
        [str(EXE), "--no-browser", "--wait-s", "30", "--port", str(port)],
        cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        url = f"http://127.0.0.1:{port}/ui/overview"
        for _ in range(120):
            try:
                raw = urllib.request.urlopen(url, timeout=1).read()
                data = json.loads(raw)["data"]
                print(f"exe 起来了：{url}")
                print(f"概览页键：{sorted(data)[:5]}")
                return 0
            except Exception:
                if proc.poll() is not None:
                    break
                time.sleep(0.5)
        out, err = proc.communicate(timeout=10)
        print("exe 没起来")
        print("stdout:", out.decode("utf-8", "replace")[-400:])
        print("stderr:", err.decode("utf-8", "replace")[-400:])
        return 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:      # pragma: no cover
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
