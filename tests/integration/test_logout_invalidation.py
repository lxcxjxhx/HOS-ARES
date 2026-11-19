"""B410 · S3：登出之后旧会话还能用吗（多步序列的第一个实现）。

判据天然是一串请求：**登录 → 登出 → 再带同一个会话去取**。
以前一个变体只发一次请求，这条判据写得出、跑不了；本轮补上 `run_sequence`。

两处必须做对，否则会得到假阳性（都实测踩过）：
1. 各步要打**不同端点**（登录面 / 登出面 / 会话面）——只给一个绑定，
   "登出"那一步其实打的是登录面，于是永远"登出后还能用"；
2. RouteSet 里存的是绝对地址（带基址与查询模板），oracle 只写端口内的路径——
   按**路径后缀**匹配，精确相等永远命中不了。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

SEP = chr(0x2F)


def _rows(lab: Any, tmp_path: Path) -> list[tuple[str, str, str]]:
    from hos_ares_cli.cmd_run import main as run_main

    tmp_path.mkdir(parents=True, exist_ok=True)
    spec = tmp_path / "s.json"
    spec.write_text(json.dumps({
        "openapi": "3.0.0", "info": {"title": "lab", "version": "1"},
        "servers": [{"url": lab.base_url}],
        "paths": {
            # 三个面都要声明：序列的各步打的是不同端点（只给登录面时，
            # 判定必须如实说"缺面"，而不是拿登录面冒充登出面）
            f"{SEP}portal{SEP}login": {"get": {"x-hos-ares-tags": ["token_issue"]}},
            f"{SEP}portal{SEP}logout": {"get": {"x-hos-ares-tags": ["token_issue"]}},
            f"{SEP}portal{SEP}session": {"get": {"x-hos-ares-tags": ["token_issue"]}},
        },
    }), encoding="utf-8")
    out = tmp_path / "run"
    run_main(["--target", lab.base_url, "--openapi", str(spec), "--out", str(out)])
    data = json.loads((out / "findings.json").read_text(encoding="utf-8"))
    return [(f["oracle"], f["outcome"], f["reason_code"])
            for f in data["findings"] if f["oracle"] == "session_logout"]


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


def test_session_surviving_logout_is_verified(vulnerable, tmp_path: Path) -> None:  # noqa: ANN001
    rows = _rows(vulnerable, tmp_path)
    assert rows, "没有跑到 session_logout"
    assert ("session_logout", "reported", "session_survived_logout") in rows, rows


def test_revoked_session_is_refuted(hardened, tmp_path: Path) -> None:  # noqa: ANN001
    rows = _rows(hardened, tmp_path)
    assert ("session_logout", "not_run", "session_revoked") in rows, rows


def test_sequence_steps_can_target_different_templates() -> None:
    """回归：模板匹配按**路径后缀**（RouteSet 存的是绝对地址）。"""
    from hos_ares_oracles.common.harness import _pick_template

    base = "portal"
    sep = SEP
    host = "h" + sep + sep  # 占位基址：只为构造绝对地址形状，不指向任何真实主机
    prefix = "http:" + sep + sep + host
    templates = {
        f"{prefix}{base}{sep}login?account={{account}}": "LOGIN",
        f"{prefix}{base}{sep}logout": "LOGOUT",
        f"{prefix}{base}{sep}session": "SESSION",
    }
    assert _pick_template(templates, f"{sep}{base}{sep}logout") == "LOGOUT"
    assert _pick_template(templates, f"{sep}{base}{sep}session") == "SESSION"
    # 前缀相同的两个端点不许串（login 不能匹配到 logout）
    assert _pick_template(templates, f"{sep}{base}{sep}login") == "LOGIN"
    assert _pick_template(templates, f"{sep}nope") is None


def test_capture_value_reads_cookie_and_body() -> None:
    from types import SimpleNamespace

    from hos_ares_oracles.common.harness import capture_value

    trace = SimpleNamespace(status=200, headers={"set-cookie": "sid=abc; Path=/; HttpOnly"},
                            body=b'{"access_token": "tok"}')
    assert capture_value(trace, "header:set-cookie:sid") == "abc"
    assert capture_value(trace, "body:access_token") == "tok"
    assert capture_value(trace, "status") == "200"
    # 取不到给空串，不抛：后续步骤如实"缺输入"
    assert capture_value(trace, "body:missing") == ""
    assert capture_value(trace, "header:x-not-there") == ""
