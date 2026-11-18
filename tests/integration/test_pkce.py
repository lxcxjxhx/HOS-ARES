"""B411 · O4：PKCE 是否真的校验 `code_verifier`。

判据靠 B410 的多步能力：取码（带 challenge）→ 用**正确** verifier 换（对照）→
再取一个码 → 用**错误** verifier 换（探针）。200 就是没校验。

一处必须做对：`redirect_uri` 要用**站内相对地址**。安全态会拒绝外域 redirect_uri，
用外域会让对照直接失败，判定落 `no_pkce_semantics`（实测踩到）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

SEP = chr(0x2F)


def _rows(lab: Any, tmp_path: Path) -> list[tuple[str, str, str]]:
    from veripen_cli.cmd_run import main as run_main

    tmp_path.mkdir(parents=True, exist_ok=True)
    spec = tmp_path / "s.json"
    spec.write_text(json.dumps({
        "openapi": "3.0.0", "info": {"title": "lab", "version": "1"},
        "servers": [{"url": lab.base_url}],
        "paths": {
            f"{SEP}portal{SEP}authorize": {"get": {"x-veripen-tags": ["token_issue"]}},
            f"{SEP}portal{SEP}token": {"get": {"x-veripen-tags": ["token_issue"]}},
        },
    }), encoding="utf-8")
    out = tmp_path / "run"
    run_main(["--target", lab.base_url, "--openapi", str(spec), "--out", str(out)])
    data = json.loads((out / "findings.json").read_text(encoding="utf-8"))
    return [(f["oracle"], f["outcome"], f["reason_code"])
            for f in data["findings"] if f["oracle"] == "pkce_verifier"]


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


def test_unchecked_verifier_is_verified(vulnerable, tmp_path: Path) -> None:  # noqa: ANN001
    rows = _rows(vulnerable, tmp_path)
    assert ("pkce_verifier", "reported", "pkce_not_enforced") in rows, rows


def test_checked_verifier_is_refuted(hardened, tmp_path: Path) -> None:  # noqa: ANN001
    rows = _rows(hardened, tmp_path)
    assert ("pkce_verifier", "not_run", "pkce_enforced") in rows, rows


def test_s256_matches_rfc7636_example() -> None:
    """RFC 7636 附录 B 的示例值：算法写错会让"正确的 verifier"换不到 token。"""
    from veripen_oracles.auth.pkce import s256

    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert s256(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_judge_pkce_tiers() -> None:
    from veripen_oracles.auth.checks import judge_pkce

    # 对照失败 → 判不了（这条面根本不做 PKCE）
    assert judge_pkce(control_ok=False, probe_status=200).verdict == "INCONCLUSIVE"
    # 错误 verifier 也被受理 → 成立
    assert judge_pkce(control_ok=True, probe_status=200).reason == "pkce_not_enforced"
    # 被拒 → 守住
    assert judge_pkce(control_ok=True, probe_status=400).reason == "pkce_enforced"
    # 既不是成功也不是拒绝 → 判不了
    assert judge_pkce(control_ok=True, probe_status=500).verdict == "INCONCLUSIVE"
