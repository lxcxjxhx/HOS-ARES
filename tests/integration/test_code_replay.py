"""B377 · 授权码重放 O5：第二条从"纯函数"接成产品可跑的 oracle。

判据（`20 §3` 取证纪律：两次都要 trace）：
- 同一个码换两次都成功 → `VERIFIED / authorization_code_replayable`；
- 第二次被拒 → `REFUTED / code_single_use`；
- 第一次就没成功 → `INCONCLUSIVE / first_exchange_failed`（不是"没问题"）。

两处**非显然但必要**的实现选择，各自有一条断言守着：
1. 基线用**另一个**码——否则基线自己消费掉一次性码，后面两发全 400，
   会把自己的探针算成"第一次换码失败"；
2. 参数里声明 `repeat=1`——这条判定天生不幂等，harness 默认发两次会把
   "一次性"读成"结果不稳定"（安全靶实测落 `INCONCLUSIVE:unstable`）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

SEP = chr(0x2F)


def _spec(base: str, path: Path) -> Path:
    path.write_text(json.dumps({
        "openapi": "3.0.0", "info": {"title": "lab", "version": "1"},
        "servers": [{"url": base}],
        "paths": {
            f"{SEP}portal{SEP}token": {"get": {"x-veripen-tags": ["token_issue"]}},
        },
    }), encoding="utf-8")
    return path


def _row(lab: Any, tmp_path: Path) -> dict[str, Any]:
    from veripen_cli.cmd_run import main as run_main

    out = tmp_path / "run"
    rc = run_main(["--target", lab.base_url,
                   "--openapi", str(_spec(lab.base_url, tmp_path / "s.json")),
                   "--out", str(out)])
    assert rc in (0, 1), rc
    data = json.loads((out / "findings.json").read_text(encoding="utf-8"))
    rows = [f for f in data["findings"] if f["oracle"] == "code_replay"]
    assert rows, f"没跑到 code_replay：{[f['oracle'] for f in data['findings']]}"
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


def test_replay_is_verified_on_a_vulnerable_target(vulnerable, tmp_path: Path) -> None:  # noqa: ANN001
    row = _row(vulnerable, tmp_path)
    assert row["outcome"] == "reported", row
    assert row["reason_code"] == "authorization_code_replayable", row


def test_single_use_is_refuted_on_a_hardened_target(hardened, tmp_path: Path) -> None:  # noqa: ANN001
    row = _row(hardened, tmp_path)
    assert row["outcome"] == "not_run", row
    assert row.get("refuted") is True, row
    assert row["reason_code"] == "code_single_use", row


def test_first_exchange_failure_is_inconclusive_not_clean() -> None:
    """第一次就没换成功：只能说"判不了"，不许当成"不能重放"。"""
    from types import SimpleNamespace

    from veripen_oracles.auth.oauth import CodeReplay
    from veripen_oracles.common.harness import Pair

    def _pair(role: str, status: int) -> Any:
        # 一个"像 Trace"的替身：判定里用到 is_normal()，缺了它测的就不是判定
        trace = SimpleNamespace(
            status=status, headers={}, error=None, body=b"{}",
            is_normal=lambda: 200 <= status < 300,
        )
        return Pair(variant=SimpleNamespace(role=role), baseline=trace, trace=trace)

    judgement = CodeReplay().judge([_pair("control", 400), _pair("probe", 400)])
    assert judgement.verdict.value == "INCONCLUSIVE", judgement
    assert judgement.reason == "first_exchange_failed", judgement
