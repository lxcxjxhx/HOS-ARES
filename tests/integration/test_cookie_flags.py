"""B390 · S2：会话 Cookie 的安全标志（HttpOnly / Secure / SameSite）。

- 脆弱靶（只给 `Path`）→ `VERIFIED / cookie_flags_missing`，证据里点名缺哪几个；
- 安全靶（三件套齐全）→ `REFUTED / cookie_flags_complete`；
- 没有 `Set-Cookie` → `INCONCLUSIVE / no_set_cookie`（不是"没问题"）。

附带修一处**误报**：`code_replay` 与 S2 共用 `token_issue` 标签，于是它会对着登录面
发两次 `code=`——登录面把未知参数一扔就 200，判定得出"授权码可重放"。
现在它的基线要求响应里有 `access_token`（真·换码接口），登录面直接 `baseline_abnormal`。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

SEP = chr(0x2F)


def _rows(lab: Any, tmp_path: Path, path_name: str) -> list[tuple[str, str, str]]:
    from hos_ares_cli.cmd_run import main as run_main

    tmp_path.mkdir(parents=True, exist_ok=True)
    spec = tmp_path / "s.json"
    spec.write_text(json.dumps({
        "openapi": "3.0.0", "info": {"title": "lab", "version": "1"},
        "servers": [{"url": lab.base_url}],
        "paths": {path_name: {"get": {"x-hos_ares-tags": ["token_issue"]}}},
    }), encoding="utf-8")
    out = tmp_path / "run"
    run_main(["--target", lab.base_url, "--openapi", str(spec), "--out", str(out)])
    data = json.loads((out / "findings.json").read_text(encoding="utf-8"))
    return [(f["oracle"], f["outcome"], f["reason_code"]) for f in data["findings"]]


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


def test_missing_cookie_flags_are_verified(vulnerable, tmp_path: Path) -> None:  # noqa: ANN001
    rows = _rows(vulnerable, tmp_path, f"{SEP}portal{SEP}login")
    assert ("cookie_flags", "reported", "cookie_flags_missing") in rows, rows


def test_complete_cookie_flags_are_refuted(hardened, tmp_path: Path) -> None:  # noqa: ANN001
    rows = _rows(hardened, tmp_path, f"{SEP}portal{SEP}login")
    assert ("cookie_flags", "not_run", "cookie_flags_complete") in rows, rows


def test_code_replay_does_not_fire_on_a_login_face(vulnerable, tmp_path: Path) -> None:  # noqa: ANN001
    """登录面没有换码语义：`code_replay` 必须自己退场，而不是判"可重放"。"""
    rows = _rows(vulnerable, tmp_path / "login", f"{SEP}portal{SEP}login")
    reasons = [r for o, _o, r in rows if o == "code_replay"]
    assert reasons and reasons[0] == "baseline_abnormal", rows


def test_secure_flag_is_not_required_over_http() -> None:
    """http 目标上缺 Secure 不算缺陷（浏览器也不会把 Secure cookie 发回来）。"""
    from hos_ares_oracles.auth.checks import judge_cookie_flags

    assert judge_cookie_flags(missing=(), https=False).verdict == "REFUTED"
    assert judge_cookie_flags(missing=("httponly",), https=False).verdict == "VERIFIED"


def test_samesite_none_counts_as_missing() -> None:
    """`SameSite=None` 等于没设：跨站照样带上。"""
    from types import SimpleNamespace

    from hos_ares_oracles.auth.cookie_flags import CookieFlags
    from hos_ares_oracles.common.harness import Pair

    trace = SimpleNamespace(
        status=200, error=None, body=b"{}", scheme="http",
        headers={"Set-Cookie": "sid=x; Path=/; HttpOnly; SameSite=None"},
        is_normal=lambda: True, fingerprint=lambda: (200, 0, ""),
    )
    def _pair(role: str) -> Pair:
        return Pair(variant=SimpleNamespace(role=role), baseline=trace, trace=trace,
                    repeats=(trace,))

    judgement = CookieFlags().judge([_pair("probe"), _pair("control")])
    assert judgement.verdict.value == "VERIFIED", judgement
    assert "samesite" in judgement.assertion["observed"]["missing"], judgement
