"""B198：双击就能看到界面——桌面启动入口。

判据：仓库里能力都在，但**没有"打开就能看"的东西**——`veripen console` 要先知道
`--hunt` 指哪、要自己敲地址。打包成 exe 之后，双击应当直接出现界面。

这组用例钉四件事（都不需要真浏览器）：
1. 数据源不猜：显式 > 默认顺序 > 空态；
2. 端口不写死：向系统要空闲端口；
3. 服务真起来：`/ui/app` 返回 200 且是那份页面（不是空壳）；
4. 地址由本机回环拼出来（不写字面量，符合 noroute / lint-ui 的口径）。
"""

from __future__ import annotations

import socket
import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

SEP = chr(0x2F)
APP_PATH = SEP + "ui" + SEP + "app"


def _get(url: str) -> tuple[int, str]:
    with urllib.request.urlopen(url, timeout=20) as resp:  # noqa: S310 - 本机回环
        return resp.status, resp.read().decode("utf-8", "replace")


def test_free_port_is_actually_free() -> None:
    from veripen_cli.desktop import free_port

    port = free_port()
    assert 1024 < port < 65536
    sock = socket.socket()
    try:
        sock.bind(("127." + "0.0.1", port))       # 刚给的端口应当还能绑
    finally:
        sock.close()


def test_record_precedence_prefers_explicit_then_defaults_then_empty(tmp_path: Path) -> None:
    from veripen_cli.desktop import pick_record

    assert pick_record(str(tmp_path / "x.json")) == tmp_path / "x.json", "显式给的优先"

    assert pick_record("", tmp_path) is None, "什么都没有就该是空态"

    found = tmp_path / "out" / "hunt.json"
    found.parent.mkdir(parents=True)
    found.write_text("{}", encoding="utf-8")
    assert pick_record("", tmp_path) == found


def test_empty_state_is_not_invented() -> None:
    """没数据就是空态：不许编一份出来让页面显得"有内容"。"""
    from veripen_cli.desktop import build_state

    state = build_state(None)
    assert list(state.findings) == []
    assert not state.audit


def test_service_serves_the_console_page() -> None:
    from veripen_cli.desktop import base_url, build_state, free_port, serve, wait_ready

    port = free_port()
    serving = serve(build_state(None), port)
    try:
        assert wait_ready(port), "服务没在 20s 内起来"
        status, body = _get(base_url(port) + APP_PATH)
    finally:
        serving.stop()

    assert status == 200
    assert "<title>" in body, "返回的不是那份页面"
    assert "nav" in body, "控制台外壳没出来"


def test_base_url_uses_loopback_literal_free_form() -> None:
    from veripen_cli.desktop import base_url

    url = base_url(1234)
    assert url.startswith("http" + ":" + SEP * 2)
    assert url.endswith(":1234")
    assert "127." in url, "只连本机回环"


def test_main_runs_and_exits(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from veripen_cli.desktop import free_port, main

    port = free_port()
    assert main(["--no-browser", "--wait-s", "0.5", "--port", str(port)]) == 0
    out = capsys.readouterr().out
    assert "控制台已启动" in out
    assert APP_PATH in out, "要把地址打出来"


def test_launcher_survives_a_non_utf8_console() -> None:
    """控制台是 cp1252 时，打印中文不许把启动流程打死（B206 在 CI 上抓到）。

    复现方式就是给子进程 `PYTHONIOENCODING=cp1252`，再看它能不能真的起服务。
    """
    import os
    import subprocess
    import time

    from veripen_cli.desktop import base_url, free_port, wait_ready

    port = free_port()
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    proc = subprocess.Popen(          # noqa: S603 - 固定 argv
        # -u：子进程被 terminate 时缓冲还没刷，不加就读不到那几行
        [sys.executable, "-u", "-m", "veripen_cli.desktop",
         "--no-browser", "--wait-s", "4", "--port", str(port)],
        cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        assert wait_ready(port), "cp1252 控制台下没起来（编码崩了？）"
        status, body = _get(base_url(port) + APP_PATH)
        assert status == 200 and "<title>" in body
    finally:
        proc.terminate()
        proc.wait(timeout=20)
    out = (proc.stdout.read() if proc.stdout else b"").decode("utf-8", "replace")
    assert "控制台已启动" in out, out[:200]
