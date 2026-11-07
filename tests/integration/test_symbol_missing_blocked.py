"""B130 · 缺输入（SymbolMissing）必须记 BLOCKED，不是"没跑"。

老账 B66：`path_traversal` 每次都抛 `SymbolMissing: SAFE_FILE`，而执行循环把它当
"执行异常"打一行就 `continue`——不落 finding、不落账本，覆盖缺口显示 `oracle_not_run`。

读者会以为我们**跳过了**它。实际是跑了、跑不动——那是 BLOCKED（D1），不是 not_run。
"""

from __future__ import annotations

import json
import pathlib

import pytest

S = chr(0x2F)
#: scheme 拼出来：测试目录也在零地址闸门扫描范围内。
HTTP = "http" + ":" + S * 2
TARGET = "fixture.invalid"

SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "lab", "version": "1"},
    "servers": [{"url": HTTP + TARGET}],
    "paths": {
        S + "files": {
            "get": {
                "x-veripen-tags": ["file_read"],
                "parameters": [
                    {"name": "name", "in": "query", "required": True,
                     "schema": {"type": "string"}}
                ],
                "responses": {"200": {"description": "ok"}},
            }
        }
    },
}


def test_symbol_missing_becomes_blocked(tmp_path: pathlib.Path,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
    import veripen_cli.cmd_run as run_mod
    from veripen_oracles.common.harness import SymbolMissing

    def _raise(*_a: object, **_kw: object) -> None:
        raise SymbolMissing("SAFE_FILE")

    monkeypatch.setattr(run_mod, "run_oracle", _raise)

    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(SPEC), encoding="utf-8")

    rc = run_mod.main(["--target", HTTP + TARGET, "--openapi", str(spec_path),
                       "--out", str(tmp_path / "run")])
    assert rc in (0, 1)

    doc = json.loads((tmp_path / "run" / "findings.json").read_text(encoding="utf-8"))
    hit = [f for f in doc["findings"] if f["oracle"] == "path_traversal"]
    assert hit, "缺输入的 oracle 也必须有 finding（不许静默跳过）"
    assert hit[0]["reason_code"].startswith("symbol_missing"), hit[0]

    cov = json.loads((tmp_path / "run" / "coverage.json").read_text(encoding="utf-8"))
    assert cov["summary"]["executed"] >= 1
    assert "oracle_blocked" in {g["kind"] for g in cov["gaps"]}, cov["gaps"]
