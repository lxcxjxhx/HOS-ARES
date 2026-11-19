"""B318 · 端到端：清单里的哈希必须对得上盘上文件。

这一族犯过两次（B316 run 三视图、B317 平台汇总）：清单用一套序列化算哈希、
文件用另一套写，Windows 上还有 CRLF 翻译。**光测"源码里有 newline=" 不够**——
这里真跑一遍，拿盘上字节去核。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

SEP = chr(0x2F)


def _spec(base_url: str, path: Path) -> Path:
    path.write_text(json.dumps({
        "openapi": "3.0.0", "info": {"title": "lab", "version": "1"},
        "servers": [{"url": base_url}],
        "paths": {f"{SEP}api{SEP}search": {"get": {"x-hos-ares-tags": ["param_injectable"]}}},
    }), encoding="utf-8")
    return path


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


def test_run_manifest_matches_the_files_on_disk(lab, tmp_path: Path) -> None:  # noqa: ANN001
    from hos_ares_cli.cmd_run import main as run_main

    out = tmp_path / "run"
    rc = run_main(["--target", lab.base_url, "--openapi", str(_spec(lab.base_url, tmp_path / "s1.json")),
                   "--out", str(out)])
    assert rc in (0, 1), rc
    manifest = json.loads((out / "report.manifest.json").read_text(encoding="utf-8"))
    assert manifest.get("files"), "清单里要有逐文件哈希"
    for name, want in manifest["files"].items():
        got = hashlib.sha256((out / name).read_bytes()).hexdigest()
        assert got == want, f"{name} 的哈希对不上盘上字节"


def test_platform_manifest_matches_the_file_on_disk(lab, tmp_path: Path) -> None:  # noqa: ANN001
    from hos_ares_cli.cmd_platform import main as platform_main

    out = tmp_path / "platform"
    platform_main(["--targets", lab.base_url,
                   "--openapi", str(_spec(lab.base_url, tmp_path / "s2.json")),
                   "--out", str(out)])
    manifest = json.loads((out / "platform-report.manifest.json").read_text(encoding="utf-8"))
    assert manifest.get("files"), "平台清单里要有逐文件哈希"
    for name, want in manifest["files"].items():
        got = hashlib.sha256((out / name).read_bytes()).hexdigest()
        assert got == want, f"{name} 的哈希对不上盘上字节"


def test_hunt_manifest_matches_the_files_on_disk(lab, tmp_path: Path) -> None:  # noqa: ANN001
    """B319：hunt 之前没有清单，于是"这两份产物没被手改"无从证明。"""
    from hos_ares_cli.cmd_hunt import main as hunt_main

    out = tmp_path / "hunt"
    rc = hunt_main(["--target", lab.base_url,
                    "--openapi", str(_spec(lab.base_url, tmp_path / "s3.json")),
                    "--out", str(out)])
    assert rc in (0, 1), rc
    manifest = json.loads((out / "hunt.manifest.json").read_text(encoding="utf-8"))
    assert manifest.get("files"), "hunt 清单里要有逐文件哈希"
    for name, want in manifest["files"].items():
        got = hashlib.sha256((out / name).read_bytes()).hexdigest()
        assert got == want, f"{name} 的哈希对不上盘上字节"
