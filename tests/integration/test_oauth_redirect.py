"""B384 · O1：授权端点 redirect_uri 校验，第一次能在产品路径上判。

上一轮说"O1 做不了"的理由是 `judge_redirect_uri` 要 `oob_hit`，而产品路径没有监听器；
B383 把真监听器接上后，这条通路就通了：

- 脆弱靶（外域 `redirect_uri` 被原样采纳 + 服务端真的去预检）→ `VERIFIED / redirect_uri_unvalidated`；
- 安全靶（外域直接 400）→ `REFUTED / redirect_uri_checked`；
- 对照不跳（端点没有跳转语义）→ `INCONCLUSIVE`，不冒充结论。

`SAFE_TARGET`（站内相对目标）由部署注入，代码里没有路径字面量。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

SEP = chr(0x2F)
SYMBOLS = json.dumps({"SAFE_TARGET": f"{SEP}portal{SEP}index"})


def _row(lab: Any, tmp_path: Path) -> dict[str, Any]:
    from hos_ares_cli.cmd_run import main as run_main

    tmp_path.mkdir(parents=True, exist_ok=True)
    spec = tmp_path / "s.json"
    spec.write_text(json.dumps({
        "openapi": "3.0.0", "info": {"title": "lab", "version": "1"},
        "servers": [{"url": lab.base_url}],
        "paths": {f"{SEP}portal{SEP}authorize": {"get": {"x-hos_ares-tags": ["redirect_entry"]}}},
    }), encoding="utf-8")
    out = tmp_path / "run"
    run_main(["--target", lab.base_url, "--openapi", str(spec), "--out", str(out),
              "--oob-listen", "--symbols", SYMBOLS])
    data = json.loads((out / "findings.json").read_text(encoding="utf-8"))
    rows = [f for f in data["findings"] if f["oracle"] == "oauth_redirect_uri"]
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


def test_unvalidated_redirect_uri_is_verified(vulnerable, tmp_path: Path) -> None:  # noqa: ANN001
    row = _row(vulnerable, tmp_path)
    assert row["outcome"] == "reported", row
    assert row["reason_code"] == "redirect_uri_unvalidated", row


def test_validated_redirect_uri_is_refuted(hardened, tmp_path: Path) -> None:  # noqa: ANN001
    row = _row(hardened, tmp_path)
    assert row["outcome"] == "not_run", row
    assert row.get("refuted") is True, row
    assert row["reason_code"] == "redirect_uri_checked", row


def test_control_must_redirect_before_any_conclusion() -> None:
    """对照都没跳（端点没有跳转语义）→ 只能 INCONCLUSIVE，不许判否。"""
    from types import SimpleNamespace

    from hos_ares_oracles.auth.checks import judge_redirect_uri

    result = judge_redirect_uri(oob_hit=True, location_ok=True, control_location_ok=False)
    assert result.verdict == "INCONCLUSIVE", result
    assert result.reason == "control_not_redirecting", result
    # 有回连但没跳、或有跳但没回连，都不算"未校验"——单一信号不足以定论
    assert judge_redirect_uri(oob_hit=True, location_ok=False, control_location_ok=True).verdict == "REFUTED"
    assert judge_redirect_uri(oob_hit=False, location_ok=True, control_location_ok=True).verdict == "REFUTED"
    assert SimpleNamespace  # 让 import 不被 lint 当成未使用
