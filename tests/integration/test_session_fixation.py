"""B376 · 会话固定 S1 第一次能从产品路径跑到。

B9 起 `auth/checks.py` 里 5 条判定函数只有单测在调，`ALL_ORACLES` 里没有 oracle 驱动，
`run`/`hunt`/`platform` 三条路径都跑不到。本批把 S1 接成真 oracle：

- 脆弱靶（服务端采纳客户端给的会话 id）→ `VERIFIED / session_not_rotated`，且落 capsule；
- 安全靶（一律换新）→ `REFUTED / session_rotated`；
- 只发 JWT、不发会话 cookie 的登录面 → `INCONCLUSIVE / no_session_cookie`
  （"没拿到 cookie"不是"没问题"）。

标签用既有的 `token_issue`（会话 cookie 也是凭据），没有新加标签——标签表是 18 §6 的
冻结字表，加一个就要改规格。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

SEP = chr(0x2F)


def _spec(base: str, path: Path, tag: str, path_name: str) -> Path:
    path.write_text(json.dumps({
        "openapi": "3.0.0", "info": {"title": "lab", "version": "1"},
        "servers": [{"url": base}],
        "paths": {path_name: {"get": {"x-hos-ares-tags": [tag]}}},
    }), encoding="utf-8")
    return path


def _run(lab: Any, tmp_path: Path, *, path_name: str, tag: str = "token_issue") -> dict[str, Any]:
    from hos_ares_cli.cmd_run import main as run_main

    out = tmp_path / "run"
    rc = run_main(["--target", lab.base_url,
                   "--openapi", str(_spec(lab.base_url, tmp_path / "s.json", tag, path_name)),
                   "--out", str(out)])
    assert rc in (0, 1), rc
    return json.loads((out / "findings.json").read_text(encoding="utf-8"))


def _row(data: dict[str, Any], oracle: str) -> dict[str, Any]:
    rows = [f for f in data["findings"] if f["oracle"] == oracle]
    assert rows, f"没跑到 {oracle}：{[f['oracle'] for f in data['findings']]}"
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


def test_fixation_is_verified_on_a_vulnerable_target(vulnerable, tmp_path: Path) -> None:  # noqa: ANN001
    row = _row(_run(vulnerable, tmp_path, path_name=f"{SEP}portal{SEP}login"), "session_fixation")
    assert row["outcome"] == "reported", row
    assert row["reason_code"] == "session_not_rotated", row
    # 结论必须带证据，否则报告层会拒绝渲染（09 §7）
    assert (tmp_path / "run" / "report.md").is_file()


def test_rotation_is_refuted_on_a_hardened_target(hardened, tmp_path: Path) -> None:  # noqa: ANN001
    row = _row(_run(hardened, tmp_path, path_name=f"{SEP}portal{SEP}login"), "session_fixation")
    assert row["outcome"] == "not_run", row
    assert row.get("refuted") is True, "判否要带 refuted 位，否则报告会写成「未执行」"
    assert row["reason_code"] == "session_rotated", row


def test_login_without_a_session_cookie_is_inconclusive() -> None:
    """只发 JWT 的登录面：拿不到 Set-Cookie，只能如实说判不了。

    这条在**判定层**验（不跑端到端）：`/api/login` 需要 query 里的 account，
    而 harness 只按模板里的 `{param}` 填参数，直连那条路会先落 `baseline_abnormal`——
    测的就变成"基线不正常"，而不是"没有会话 cookie"。判据本身要单独钉住。
    """
    from types import SimpleNamespace

    from hos_ares_oracles.auth.session import SessionFixation
    from hos_ares_oracles.common.harness import Pair

    def _pair(role: str, headers: dict[str, str]) -> Any:
        trace = SimpleNamespace(status=200, headers=headers, error=None, body=b"{}")
        return Pair(variant=SimpleNamespace(role=role), baseline=trace, trace=trace)

    judgement = SessionFixation().judge([
        _pair("control", {"Content-Type": "application/json"}),
        _pair("probe", {"Content-Type": "application/json"}),
    ])
    assert judgement.verdict.value == "INCONCLUSIVE", judgement
    assert judgement.reason == "no_session_cookie", judgement
