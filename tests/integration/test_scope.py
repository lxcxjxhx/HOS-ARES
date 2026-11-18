"""B412 · O8：请求超出授权的 scope，服务端会不会真的授予。

判据靠 B410 的多步能力：正常 scope 取码换 token → 访问受限资源（对照，必须被拒）
→ 请求超范围 scope 取码换 token → 再访问同一个受限资源（探针，200 就是授予了）。

**对照那一步不能省**：如果正常 scope 的 token 也能进，"提升"就无从谈起，
那是「这个面不看 scope」，判定必须退成判不了（实测就是靠这一步区分两种缺陷）。
"""

from __future__ import annotations

_SLASH = chr(0x2F)

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
            f"{SEP}portal{SEP}admin": {"get": {"x-veripen-tags": ["token_issue"]}},
        },
    }), encoding="utf-8")
    out = tmp_path / "run"
    run_main(["--target", lab.base_url, "--openapi", str(spec), "--out", str(out)])
    data = json.loads((out / "findings.json").read_text(encoding="utf-8"))
    return [(f["oracle"], f["outcome"], f["reason_code"])
            for f in data["findings"] if f["oracle"] == "scope_escalation"]


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


def test_granted_wide_scope_is_verified(vulnerable, tmp_path: Path) -> None:  # noqa: ANN001
    rows = _rows(vulnerable, tmp_path)
    assert ("scope_escalation", "reported", "scope_not_enforced") in rows, rows


def test_rejected_wide_scope_is_refuted(hardened, tmp_path: Path) -> None:  # noqa: ANN001
    rows = _rows(hardened, tmp_path)
    assert ("scope_escalation", "not_run", "scope_request_rejected") in rows, rows


def test_control_token_is_denied_in_both_modes() -> None:
    """对照必须在两种模式下都被拒：否则探针的 200 分不清"提升"与"没鉴权"。"""
    from lab.app import App

    for secure in (False, True):
        app = App(secure=secure)
        env = {"REQUEST_METHOD": "GET", "PATH_INFO": _SLASH + "portal" + _SLASH + "authorize",
               "QUERY_STRING": "redirect_uri=/cb&state=s1&scope=read"}

        def start(_status: str, _headers: list) -> None:
            return None

        code = json.loads(b"".join(app(env, start)))["code"]
        env2 = {"REQUEST_METHOD": "GET", "PATH_INFO": _SLASH + "portal" + _SLASH + "token",
                "QUERY_STRING": f"code={code}"}
        token = json.loads(b"".join(app(env2, start)))["access_token"]
        env3 = {"REQUEST_METHOD": "GET", "PATH_INFO": _SLASH + "portal" + _SLASH + "admin",
                "HTTP_AUTHORIZATION": f"Bearer {token}"}
        box: dict = {}

        def start3(status: str, _headers: list) -> None:
            box["status"] = int(status.split()[0])

        b"".join(app(env3, start3))
        assert box["status"] == 403, (secure, box)


def test_judge_scope_tiers() -> None:
    from veripen_oracles.auth.checks import judge_scope_escalation

    # 授权阶段就拒了 → 守住（不必再看资源端）
    assert judge_scope_escalation(
        control_denied=False, scope_granted=False, probe_status=401).reason == (
        "scope_request_rejected")
    # 对照没被拒 → 这个面不看 scope，判不了
    assert judge_scope_escalation(
        control_denied=False, scope_granted=True, probe_status=200).reason == (
        "no_scope_semantics")
    # 对照被拒 + 探针 200 → 提升成立
    assert judge_scope_escalation(
        control_denied=True, scope_granted=True, probe_status=200).reason == (
        "scope_not_enforced")
    # 对照被拒 + 探针 403 → 守住
    assert judge_scope_escalation(
        control_denied=True, scope_granted=True, probe_status=403).reason == (
        "scope_enforced")
    # 既不是放行也不是拒绝 → 判不了
    assert judge_scope_escalation(
        control_denied=True, scope_granted=True, probe_status=500).verdict == "INCONCLUSIVE"