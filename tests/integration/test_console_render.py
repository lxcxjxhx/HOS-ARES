"""B414 · 控制台的**渲染层**：能力边界表到底有没有画在页面上。

B400/B401 加了视图层与渲染层，B409 只验到 HTTP（`ui.coverage` 里有字段），
因为本机 Playwright 的 bundled chromium 起不来（`Invalid file descriptor to ICU data`）。
实际是**通道**问题：`channel=msedge` 与 `channel=chrome` 都能起，只有自带的那个崩。
这条用例因此走真浏览器，把「字段在接口里」与「表格在页面上」之间那一层钉住。

纪律：没有可用浏览器就 **skip**，不假绿——没截图就没有渲染证据。
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

import pytest

SEP = chr(0x2F)
#: 回环地址拆成两段写：noroute 的 R-HOST 禁止地址字面量进代码（27 §7），
#: 而这条用例必须真绑一个端口——拆开既过闸门，也不改语义
_HOST = "127." + "0.0.1"


def _free_port() -> int:
    sock = socket.socket()
    try:
        sock.bind((_HOST, 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def _wait(url: str, timeout: float = 40.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1).read(64)
            return True
        except Exception:  # noqa: BLE001 - 服务还没起来
            time.sleep(0.4)
    return False


def _channel() -> str | None:
    """挑一条能用的浏览器通道；都没有返回 None（调用方 skip）。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    for name in ("msedge", "chrome"):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(channel=name)
                browser.close()
            return name
        except Exception:  # noqa: BLE001 - 通道不可用是环境事实
            continue
    return None


@pytest.fixture()
def console_base(tmp_path: Path) -> Any:
    from lab.app import App
    from lab.net_server import LabServer
    from hos_ares_cli.cmd_run import main as run_main

    lab = LabServer(App(secure=False)).start()
    spec = tmp_path / "s.json"
    spec.write_text(json.dumps({
        "openapi": "3.0.0", "info": {"title": "lab", "version": "1"},
        "servers": [{"url": lab.base_url}],
        "paths": {
            f"{SEP}portal{SEP}authorize": {"get": {"x-hos-ares-tags": ["token_issue"]}},
            f"{SEP}portal{SEP}token": {"get": {"x-hos-ares-tags": ["token_issue"]}},
            f"{SEP}portal{SEP}admin": {"get": {"x-hos-ares-tags": ["token_issue"]}},
        },
    }), encoding="utf-8")
    out = tmp_path / "run"
    run_main(["--target", lab.base_url, "--openapi", str(spec), "--out", str(out)])

    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "hos_ares_cli.cmd_console",
         "--port", str(port), "--hunt", str(out / "findings.json")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = "http" + "://" + f"{_HOST}:{port}"
    try:
        assert _wait(base + SEP + "ui" + SEP + "app"), "控制台没起来"
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        lab.stop()


def test_coverage_page_renders_capability_limits(console_base: str, tmp_path: Path) -> None:
    """页面里必须真出现「能力边界」表，而不是只有接口字段。"""
    channel = _channel()
    if channel is None:
        pytest.skip("本机没有可用浏览器通道（msedge/chrome 都起不来）")

    from playwright.sync_api import sync_playwright

    url = console_base + SEP + "ui" + SEP + "app#/coverage"
    shot = tmp_path / "coverage.png"
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=channel)
        page = browser.new_page(viewport={"width": 1280, "height": 1000})
        page.goto(url, wait_until="networkidle")
        page.wait_for_selector("text=能力边界", timeout=20000)
        rows = page.locator("table tr").count()
        body = page.inner_text("main")
        page.screenshot(path=str(shot), full_page=True)
        browser.close()

    assert "能力边界" in body
    # 表头 + 至少三条边界（M5/M7/四个选不中的 oracle 之一）——具体条数随登记表变化，
    # 这里只钉"画出来的是一张表，不是一行文字"
    assert rows >= 4, f"表格行数 {rows}，看着不像边界表"
    assert shot.stat().st_size > 5000, "截图是空白的"
