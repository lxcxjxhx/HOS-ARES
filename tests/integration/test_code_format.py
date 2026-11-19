"""B391 · O7：换码端点是否校验授权码的形式。

- 脆弱靶（任何非空字符串都往下走）→ `VERIFIED / code_format_unchecked`，证据里点名被受理的形状；
- 安全靶（16 位小写十六进制，其余 400）→ `REFUTED / code_format_checked`。

顺带修一处**自己挖的坑**：`code_replay` 的固定码原来是 `vp-fixed-code` 这种串，
靶场的安全态一加形式校验就先把基线的码 400 掉，于是它永远`baseline_abnormal`。
判定用的码必须是目标能接受的形状。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

SEP = chr(0x2F)


def _row(lab: Any, tmp_path: Path) -> dict[str, Any]:
    from hos_ares_cli.cmd_run import main as run_main

    tmp_path.mkdir(parents=True, exist_ok=True)
    spec = tmp_path / "s.json"
    spec.write_text(json.dumps({
        "openapi": "3.0.0", "info": {"title": "lab", "version": "1"},
        "servers": [{"url": lab.base_url}],
        "paths": {f"{SEP}portal{SEP}token": {"get": {"x-hos-ares-tags": ["token_issue"]}}},
    }), encoding="utf-8")
    out = tmp_path / "run"
    run_main(["--target", lab.base_url, "--openapi", str(spec), "--out", str(out)])
    data = json.loads((out / "findings.json").read_text(encoding="utf-8"))
    rows = [f for f in data["findings"] if f["oracle"] == "code_format"]
    assert rows, [(f["oracle"], f["reason_code"]) for f in data["findings"]]
    return rows[0]


@pytest.fixture()
def vulnerable():  # noqa: ANN201
    from lab.app import App
    from lab.net_server import LabServer

    srv = LabServer(App(secure=False)).start()
    try:
        yield srv
    finally:
        srv.stop()


@pytest.fixture()
def hardened():  # noqa: ANN201
    from lab.app import App
    from lab.net_server import LabServer

    srv = LabServer(App(secure=True)).start()
    try:
        yield srv
    finally:
        srv.stop()


def test_unchecked_format_is_verified(vulnerable, tmp_path: Path) -> None:  # noqa: ANN001
    row = _row(vulnerable, tmp_path)
    assert row["outcome"] == "reported", row
    assert row["reason_code"] == "code_format_unchecked", row


def test_checked_format_is_refuted(hardened, tmp_path: Path) -> None:  # noqa: ANN001
    row = _row(hardened, tmp_path)
    assert row["outcome"] == "not_run", row
    assert row.get("refuted") is True, row
    assert row["reason_code"] == "code_format_checked", row


def test_one_accepted_shape_is_enough_to_convict() -> None:
    from hos_ares_oracles.auth.checks import judge_code_format

    result = judge_code_format(malformed_accepted=("too_short",), probe_count=5)
    assert result.verdict == "VERIFIED", result
    assert result.reason == "code_format_unchecked", result
    assert judge_code_format(malformed_accepted=(), probe_count=5).verdict == "REFUTED"
    # 一个 probe 都没发出去：判不了，不许当"通过"
    assert judge_code_format(malformed_accepted=(), probe_count=0).verdict == "INCONCLUSIVE"


def test_evidence_reports_shapes_not_values() -> None:
    """畸形码的值不进证据：它可能被当成凭据被复用。"""
    from types import SimpleNamespace

    from hos_ares_oracles.auth.code_format import CodeFormat
    from hos_ares_oracles.common.harness import Pair

    def _pair(role: str, status: int, body: bytes, name: str = "") -> Pair:
        trace = SimpleNamespace(status=status, error=None, body=body, headers={},
                                scheme="http", is_normal=lambda: 200 <= status < 300,
                                fingerprint=lambda: (status, 0, ""))
        return Pair(variant=SimpleNamespace(role=role, name=name or f"format_{role}"),
                    baseline=trace, trace=trace, repeats=(trace,))

    judgement = CodeFormat().judge([
        _pair("control", 200, b'{"access_token": "t"}'),
        _pair("probe", 200, b'{"access_token": "t"}', name="format_too_short"),
    ])
    assert judgement.verdict.value == "VERIFIED", judgement
    observed = judgement.assertion["observed"]
    assert observed["accepted_shapes"] == ["too_short"], observed
    # 证据里不该出现畸形码本身
    assert "x" not in json.dumps(observed, ensure_ascii=False).replace("shapes", "")
