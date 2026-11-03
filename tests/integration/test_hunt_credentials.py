"""B34 · 凭据不得出现在命令行与产物里。

判据只有一条，而且能红：**同一个 token，从文件读进来跑完一轮，`hunt.json`
与 stdout 里都必须搜不到它**。把 token 塞进 record 或 print 一次，这条测试立刻失败。

为什么单列一批：B28b 用 `--token <明文>` 传凭据，那会同时进 shell 历史、
进程列表（`ps` 可见）与 CI 日志。三处都得手擦，且擦不干净——
`hunt.json` 是证据文件，最容易被复制进报告。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT, ROOT / "tests", ROOT / "tests" / "lab"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

SEP = chr(0x2F)

#: 一个肉眼能认出来的假凭据。真实 token 不会长这样，但判据只看"有没有原样出现"。
CANARY = "vp-canary-do-not-leak-7f3a9c"


def _spec(base: str) -> str:
    paths = {
        f"{SEP}api{SEP}ping": {"get": {"x-veripen-tags": ["rate_sensitive"]}},
        f"{SEP}api{SEP}me": {"get": {"x-veripen-tags": ["protected_read"]}},
    }
    return json.dumps(
        {
            "openapi": "3.0.0",
            "info": {"title": "t", "version": "1"},
            "servers": [{"url": base}],
            "paths": paths,
        }
    )


@pytest.fixture()
def lab():  # noqa: ANN201
    from lab.app import App
    from lab.net_server import LabServer

    srv = LabServer(App(secure=False)).start()
    try:
        yield srv
    finally:
        srv.stop()


def test_token_file_is_not_echoed_anywhere(lab: Any, tmp_path: Path, capsys: Any) -> None:
    """从文件读凭据，产物与标准输出都不许出现明文。"""
    from veripen_cli.cmd_hunt import main

    token_file = tmp_path / "primary.token"
    token_file.write_text(CANARY + "\n", encoding="utf-8")
    spec = tmp_path / "spec.json"
    spec.write_text(_spec(lab.base_url), encoding="utf-8")
    out = tmp_path / "out"

    rc = main([
        "--target", lab.base_url,
        "--openapi", str(spec),
        "--token-file", str(token_file),
        "--out", str(out),
        "--max-steps", "4",
    ])
    captured = capsys.readouterr()
    record = (out / "hunt.json").read_text(encoding="utf-8")

    assert rc == 0, captured.err
    assert CANARY not in record, "hunt.json 里出现了凭据明文"
    assert CANARY not in captured.out, "stdout 里出现了凭据明文"
    assert CANARY not in captured.err, "stderr 里出现了凭据明文"


def test_cli_token_still_works_but_warns(lab: Any, tmp_path: Path, capsys: Any) -> None:
    """兼容路径仍可用，但必须告警——不告警等于默认推荐它。"""
    from veripen_cli.cmd_hunt import main

    spec = tmp_path / "spec.json"
    spec.write_text(_spec(lab.base_url), encoding="utf-8")
    main([
        "--target", lab.base_url,
        "--openapi", str(spec),
        "--token", CANARY,
        "--out", str(tmp_path / "out"),
        "--max-steps", "3",
    ])
    captured = capsys.readouterr()
    assert "shell 历史" in captured.err, captured.err
    # 告警里也不许复述凭据本身
    assert CANARY not in captured.err


def test_secret_priority_file_over_env_over_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """文件 > 环境变量 > 命令行。顺序错了就等于把最危险的那条设成默认。"""
    from veripen_cli.cmd_hunt import _read_secret

    f = tmp_path / "t.token"
    f.write_text("from-file", encoding="utf-8")
    monkeypatch.setenv("VERIPEN_TARGET_TOKEN", "from-env")

    assert _read_secret("from-cli", str(f), "VERIPEN_TARGET_TOKEN", "t") == "from-file"
    assert _read_secret("from-cli", "", "VERIPEN_TARGET_TOKEN", "t") == "from-env"
    monkeypatch.delenv("VERIPEN_TARGET_TOKEN")
    assert _read_secret("from-cli", "", "VERIPEN_TARGET_TOKEN", "t") == "from-cli"
    assert _read_secret("", "", "VERIPEN_TARGET_TOKEN", "t") == ""
