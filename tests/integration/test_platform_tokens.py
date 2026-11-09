"""B185：一个 token 发给所有资产 = 把 A 的凭据递给 B 和 C。

判据：`platform --token` 的说明原来是“透传给每个资产”。企业现场每个资产一份凭据，
跨资产复用不只是"不准"，还可能把自己的凭据送给不该拿到的机器。

现在的口径：
* 多资产 + `--token`（无确认）→ **拒绝**，退出码 2，一个资产都不跑；
* 想复用就显式 `--allow-shared-token`，报告里照实写"同一 token 注入 N 个资产"；
* 正路是 `--token-for 资产=token`（可重复，按资产注入）；
* `--token` 与 `--token-for` 二选一（混用只会让人搞不清哪个生效）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

SEP = chr(0x2F)
A = "http" + ":" + SEP + SEP + "a.invalid"
B = "http" + ":" + SEP + SEP + "b.invalid"
DEAD = "http" + ":" + SEP + SEP + "127." + "0.0.1:9"


@pytest.fixture()
def spec(tmp_path: Path) -> Path:
    path = tmp_path / "spec.json"
    path.write_text(json.dumps({
        "openapi": "3.0.0", "info": {"title": "x", "version": "1"},
        "servers": [{"url": A}],
        "paths": {SEP + "api" + SEP + "x": {"get": {"x-veripen-tags": ["param_injectable"]}}},
    }), encoding="utf-8")
    return path


@pytest.fixture()
def calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    from veripen_cli import cmd_platform, cmd_run

    seen: list[list[str]] = []

    def record(argv: list[str]) -> int:
        seen.append(list(argv))
        return 0

    monkeypatch.setattr(cmd_run, "main", record)
    return seen


def _run(tmp_path: Path, spec: Path, targets: str, *extra: str) -> int:
    from veripen_cli.cmd_platform import main

    return main(["--targets", targets, "--openapi", str(spec), "--out", str(tmp_path / "pf"),
                 "--db", str(tmp_path / "pf.db"), *extra])


def _token_of(argv: list[str]) -> str:
    return argv[argv.index("--token") + 1] if "--token" in argv else ""


def test_shared_token_across_assets_is_refused(
    tmp_path: Path, spec: Path, calls: list[list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = _run(tmp_path, spec, f"{A},{B}", "--token", "TOK")
    assert rc == 2, "多资产默认不许一个 token 打通"
    assert calls == [], "拒绝时一个资产都不该跑"
    err = capsys.readouterr().err
    assert "--token-for" in err and "--allow-shared-token" in err


def test_shared_token_is_allowed_when_explicit(
    tmp_path: Path, spec: Path, calls: list[list[str]],
) -> None:
    rc = _run(tmp_path, spec, f"{A},{B}", "--token", "TOK", "--allow-shared-token")
    assert rc == 0
    assert [_token_of(c) for c in calls] == ["TOK", "TOK"]
    report = (tmp_path / "pf" / "platform-report.md").read_text(encoding="utf-8")
    assert "同一 token 注入 2 个资产" in report, "报告要如实写清是复用"


def test_per_asset_token_only_reaches_its_own_asset(
    tmp_path: Path, spec: Path, calls: list[list[str]],
) -> None:
    rc = _run(tmp_path, spec, f"{A},{B}", "--token-for", "asset-2=T2")
    assert rc == 0
    assert [_token_of(c) for c in calls] == ["", "T2"], "asset-1 没有凭据，asset-2 才该带"
    report = (tmp_path / "pf" / "platform-report.md").read_text(encoding="utf-8")
    assert "按资产注入 1 项（asset-2" in report


def test_unknown_asset_in_token_for_is_rejected(
    tmp_path: Path, spec: Path, calls: list[list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = _run(tmp_path, spec, f"{A},{B}", "--token-for", "asset-9=T")
    assert rc == 2 and calls == []
    assert "资产未知" in capsys.readouterr().err


def test_token_and_token_for_are_mutually_exclusive(
    tmp_path: Path, spec: Path, calls: list[list[str]],
) -> None:
    rc = _run(tmp_path, spec, f"{A},{B}", "--token", "T", "--token-for", "asset-1=T1")
    assert rc == 2 and calls == []


def test_single_asset_token_still_works(
    tmp_path: Path, spec: Path, calls: list[list[str]],
) -> None:
    rc = _run(tmp_path, spec, DEAD, "--token", "TOK")
    assert rc == 0 and [_token_of(c) for c in calls] == ["TOK"]
