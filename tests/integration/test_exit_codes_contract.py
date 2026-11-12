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
