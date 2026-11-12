"""B344 · 退出码契约：README 那张表要能被用例逐格验证。

表在 README「退出码（写脚本 / 接 CI 用）」一节，三条主命令各有四档。
这里把能低成本复现的格子钉住；成本高的（如全资产跑完）由各自的集成用例覆盖。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

SEP = chr(0x2F)
LOOPBACK_DEAD = "http" + ":" + SEP * 2 + "127." + "0.0.1:1"


def _lab():  # noqa: ANN202
    from lab.app import App
    from lab.net_server import LabServer

    return LabServer(App(secure=False)).start()


@pytest.fixture(scope="module")
def lab():  # noqa: ANN201
    server = _lab()
    try:
        yield server
    finally:
        server.stop()


def _spec(base: str, path: Path) -> Path:
    path.write_text(json.dumps({
        "openapi": "3.0.0", "info": {"title": "lab", "version": "1"},
        "servers": [{"url": base}],
        "paths": {f"{SEP}api{SEP}search": {"get": {"x-veripen-tags": ["param_injectable"]}}},
    }), encoding="utf-8")
    return path


def test_run_success_is_zero(lab, tmp_path: Path) -> None:  # noqa: ANN001
    from veripen_cli.cmd_run import main as run_main

    rc = run_main(["--target", lab.base_url,
                   "--openapi", str(_spec(lab.base_url, tmp_path / "s.json")),
                   "--out", str(tmp_path / "run")])
    assert rc == 0, rc


def test_run_input_error_is_two(tmp_path: Path) -> None:
    from veripen_cli.cmd_run import main as run_main

    with pytest.raises(SystemExit) as exc:
        run_main(["--target", "not-a-url", "--openapi", "x.json", "--out", str(tmp_path)])
    assert exc.value.code == 2


def test_run_nothing_verified_with_errors_is_one(tmp_path: Path) -> None:
    from veripen_cli.cmd_run import main as run_main

    rc = run_main(["--target", LOOPBACK_DEAD,
                   "--openapi", str(_spec(LOOPBACK_DEAD, tmp_path / "s.json")),
                   "--out", str(tmp_path / "run")])
    assert rc == 1, rc


def test_hunt_input_error_is_two() -> None:
    from veripen_cli.cmd_hunt import main as hunt_main

    with pytest.raises(SystemExit) as exc:
        hunt_main(["--target", "not-a-url", "--openapi", "x.json"])
    assert exc.value.code == 2


def test_platform_approval_pending_is_three(lab, tmp_path: Path) -> None:  # noqa: ANN001
    """`3` 单独留出来，是为了让 CI 能区分"等人签字"与"跑挂了"。"""
    import os

    from veripen_cli.cmd_platform import main as platform_main

    os.environ["VP_ASSET_WEB_01"] = lab.base_url
    rc = platform_main(["--profile", "profiles" + SEP + "demo-a" + SEP + "profile.yaml",
                        "--openapi", str(_spec(lab.base_url, tmp_path / "s.json")),
                        "--out", str(tmp_path / "plat"), "--require-approval"])
    assert rc == 3, rc


def test_platform_input_error_is_two(tmp_path: Path) -> None:
    from veripen_cli.cmd_platform import main as platform_main

    rc = platform_main(["--profile", str(tmp_path / "nope.yaml"),
                        "--openapi", "x.json", "--out", str(tmp_path / "o")])
    assert rc == 2, rc


def test_hunt_contract_without_tags_is_one(tmp_path: Path) -> None:
    """表里的另一格：契约里没有带语义标签的 operation → 1（不是 2，参数没错）。"""
    from veripen_cli.cmd_hunt import main as hunt_main

    spec = tmp_path / "untagged.json"
    spec.write_text(json.dumps({
        "openapi": "3.0.0", "info": {"title": "x", "version": "1"},
        "servers": [{"url": LOOPBACK_DEAD}],
        "paths": {f"{SEP}plain": {"get": {}}},
    }), encoding="utf-8")
    rc = hunt_main(["--target", LOOPBACK_DEAD, "--openapi", str(spec),
                    "--out", str(tmp_path / "hunt")])
    assert rc == 1, rc


def test_platform_with_one_bad_asset_is_one(lab, tmp_path: Path) -> None:  # noqa: ANN001
    """一个资产跑成功、另一个不可达 → 有资产退出码非 0，报告里标 —，整体 1。"""
    from veripen_cli.cmd_platform import main as platform_main

    rc = platform_main(["--targets", f"{lab.base_url},{LOOPBACK_DEAD}",
                        "--openapi", str(_spec(lab.base_url, tmp_path / "s.json")),
                        "--out", str(tmp_path / "plat")])
    assert rc == 1, rc


# ── 长驻命令的两档（0 / 2）：与三条主命令并列写在同一个契约文件里 ──
def _run_cli(args: list[str], timeout: int = 60):  # noqa: ANN202
    import subprocess
    import sys

    return subprocess.run([sys.executable, "-m", "veripen_cli", *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)


def test_chat_bad_args_is_two() -> None:
    assert _run_cli(["chat", "--db"]).returncode == 2


def test_agent_bad_root_is_two() -> None:
    assert _run_cli(["agent", "--root", "no/such/dir"]).returncode == 2


def test_tui_bad_args_is_two() -> None:
    assert _run_cli(["tui", "--steps", "abc"]).returncode == 2


def test_console_busy_port_is_two() -> None:
    """端口被占 = 起不来 = 2（不是 1，也不是 0）。"""
    import socket

    sock = socket.socket()
    try:
        sock.bind(("127." + "0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        proc = _run_cli(["console", "--port", str(port)], timeout=30)
        assert proc.returncode == 2, (proc.returncode, proc.stderr[-200:])
    finally:
        sock.close()


def test_all_blocked_by_deny_is_one(lab, tmp_path: Path) -> None:  # noqa: ANN001
    """全被黑名单挡下 = 什么都没证明 → 1（改前是 0，CI 会当绿）。"""
    from veripen_cli.cmd_run import main as run_main

    rc = run_main(["--target", lab.base_url,
                   "--openapi", str(_spec(lab.base_url, tmp_path / "s.json")),
                   "--out", str(tmp_path / "run"), "--deny", "DENY_SET_DEFAULT"])
    assert rc == 1, rc


def test_platform_deny_is_honoured_per_asset(lab, tmp_path: Path) -> None:  # noqa: ANN001
    """平台原来没有 --deny 入口（只有客户档能写）——多资产最危险，入口最弱。"""
    import json as _json

    from veripen_cli.cmd_platform import main as platform_main

    rc = platform_main(["--targets", lab.base_url,
                        "--openapi", str(_spec(lab.base_url, tmp_path / "s.json")),
                        "--out", str(tmp_path / "plat"), "--deny", "DENY_SET_DEFAULT"])
    assert rc == 1, rc
    bounds = _json.loads((tmp_path / "plat" / "asset-1" / "bounds.json").read_text(encoding="utf-8"))
    assert bounds["count"] >= 1, bounds
