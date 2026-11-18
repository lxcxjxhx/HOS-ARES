"""B389 · O2：授权请求的 `state` 是否被强制与回传。

- 脆弱靶（忽略 state、不带也受理）→ `VERIFIED / state_not_enforced`；
- 安全靶（缺 state 直接 400、给了就原样回传）→ `REFUTED / state_enforced`；
- 对照不跳 → `INCONCLUSIVE`，不冒充结论。

两处实现细节，各配一条断言：
1. **基线必须带 state**：真服务端会要求它，不带就是畸形请求，基线 400 →
   `baseline_abnormal` → 整条判定跑不起来（实测撞过）；
2. **probe 用 `extra_params` 把 state 抹成空串**：`apply()` 先铺基线再加 extra，
   所以这是"不带"的唯一表达方式（`transform` 只能改模板参数）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

SEP = chr(0x2F)
SYMBOLS = json.dumps({"SAFE_TARGET": f"{SEP}portal{SEP}index"})


def _row(lab: Any, tmp_path: Path) -> dict[str, Any]:
    from veripen_cli.cmd_run import main as run_main

    tmp_path.mkdir(parents=True, exist_ok=True)
    spec = tmp_path / "s.json"
    spec.write_text(json.dumps({
        "openapi": "3.0.0", "info": {"title": "lab", "version": "1"},
        "servers": [{"url": lab.base_url}],
        "paths": {f"{SEP}portal{SEP}authorize": {"get": {"x-veripen-tags": ["redirect_entry"]}}},
    }), encoding="utf-8")
    out = tmp_path / "run"
    run_main(["--target", lab.base_url, "--openapi", str(spec), "--out", str(out),
              "--symbols", SYMBOLS])
    data = json.loads((out / "findings.json").read_text(encoding="utf-8"))
    rows = [f for f in data["findings"] if f["oracle"] == "oauth_state"]
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


def test_missing_state_is_verified(vulnerable, tmp_path: Path) -> None:  # noqa: ANN001
    row = _row(vulnerable, tmp_path)
    assert row["outcome"] == "reported", row
    assert row["reason_code"] == "state_not_enforced", row


def test_enforced_state_is_refuted(hardened, tmp_path: Path) -> None:  # noqa: ANN001
    row = _row(hardened, tmp_path)
    assert row["outcome"] == "not_run", row
    assert row.get("refuted") is True, row
    assert row["reason_code"] == "state_enforced", row


def test_control_must_redirect_before_judging() -> None:
    from veripen_oracles.auth.checks import judge_state_param

    result = judge_state_param(control_redirects=False, control_echoed=True, probe_accepted=True)
    assert result.verdict == "INCONCLUSIVE", result
    assert result.reason == "control_not_redirecting", result


def test_accepted_probe_convicts_even_without_echo() -> None:
    """受理本身就够定罪：回不回传是另一件事。"""
    from veripen_oracles.auth.checks import judge_state_param

    result = judge_state_param(control_redirects=True, control_echoed=False, probe_accepted=True)
    assert result.verdict == "VERIFIED", result
    assert result.reason == "state_not_enforced", result


def test_no_echo_and_no_acceptance_is_inconclusive() -> None:
    from veripen_oracles.auth.checks import judge_state_param

    result = judge_state_param(control_redirects=True, control_echoed=False, probe_accepted=False)
    assert result.verdict == "INCONCLUSIVE", result
    assert result.reason == "state_not_observed", result
