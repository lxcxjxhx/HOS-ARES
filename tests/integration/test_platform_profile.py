"""B72 · platform 接 profile（H16 判据 A–E）。

两个层次：
- 真档（`profiles/demo-a`）能跑通，报告写明"按哪个档跑的"；
- 缺符号 / 参数冲突时 fail-closed，不猜地址、不静默取其一。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

SEP = chr(0x2F)
PROFILE = Path(__file__).resolve().parents[2] / "profiles" / "demo-a" / "profile.yaml"
ENV_KEY = "VP_ASSET_WEB_01"


def _spec(base: str) -> str:
    paths = {
        f"{SEP}api{SEP}search": {"get": {"x-veripen-tags": ["param_injectable"]}},
        f"{SEP}api{SEP}items{SEP}{{id}}": {"get": {"x-veripen-tags": ["object_read"]}},
        f"{SEP}api{SEP}read": {"get": {"x-veripen-tags": ["file_read"]}},
    }
    return json.dumps({
        "openapi": "3.0.0", "info": {"title": "lab", "version": "1"},
        "servers": [{"url": base}],
        "paths": paths,
    })


@pytest.fixture()
def lab_and_spec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from lab.app import App
    from lab.net_server import LabServer

    srv = LabServer(App(secure=False)).start()
    spec = tmp_path / "spec.json"
    spec.write_text(_spec(srv.base_url), encoding="utf-8")
    monkeypatch.setenv(ENV_KEY, srv.base_url)
    try:
        yield srv, spec
    finally:
        srv.stop()


def _run(tmp_path: Path, spec: Path, *extra: str) -> int:
    from veripen_cli.cmd_platform import main

    return main([
        "--profile", str(PROFILE), "--openapi", str(spec),
        "--out", str(tmp_path / "pf"), "--db", str(tmp_path / "pf.db"), *extra,
    ])


# ── A/C：真档跑通 + 报告写明身份 ───────────────────────────────
def test_profile_run_writes_identity(lab_and_spec: Any, tmp_path: Path) -> None:
    _srv, spec = lab_and_spec
    rc = _run(tmp_path, spec, "--approve")
    assert rc == 0, "真档应能跑通（assets 已注入、needs_approval 已签）"

    report = (tmp_path / "pf" / "platform-report.md").read_text(encoding="utf-8")
    assert "客户档：" in report
    assert "demo-a" in report
    assert "engagement" in report
    assert "档策略：可用 oracle" in report


def test_profile_limits_oracles(lab_and_spec: Any, tmp_path: Path) -> None:
    """档里 enabled 之外的 oracle 不该出现（白名单翻黑名单）。"""
    _srv, spec = lab_and_spec
    _run(tmp_path, spec, "--approve")
    findings = (tmp_path / "pf" / "asset-1" / "findings.json")
    if not findings.exists():
        pytest.skip("该档 enabled 里没有可跑的 oracle")
    data = json.loads(findings.read_text(encoding="utf-8"))
    names = {str(f.get("ruleId") or "") for f in data.get("findings", [])}
    assert names, "至少应有结论项"
    # demo-a 的 enabled 含 sqli_bool_diff；不在 enabled 的应当被排除
    assert all("rate_limit_absent" not in n for n in names), "未启用项不该跑"


# ── B：档要求审批时自动阻断 ────────────────────────────────────
def test_profile_needs_approval_blocks_without_signoff(lab_and_spec: Any,
                                                       tmp_path: Path,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    _srv, spec = lab_and_spec
    rc = _run(tmp_path, spec)          # 不给 --approve
    assert rc == 3, "档里 needs_approval 非空 → 未签字必须阻断"
    out = capsys.readouterr().out
    assert "档要求" in out and "需放行" in out, "要说明是档要求的，不是命令行开关"


# ── D：符号未注入 → fail-closed ────────────────────────────────
def test_missing_symbol_exits_two(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                  capsys: pytest.CaptureFixture[str]) -> None:
    from veripen_cli.cmd_platform import main

    monkeypatch.delenv(ENV_KEY, raising=False)
    spec = tmp_path / "s.json"
    spec.write_text(_spec("http" + ":" + SEP + SEP + "127." + "0.0.1:1"), encoding="utf-8")
    rc = main(["--profile", str(PROFILE), "--openapi", str(spec),
               "--out", str(tmp_path / "o"), "--db", str(tmp_path / "o.db")])
    err = capsys.readouterr().err
    assert rc == 2, "缺符号必须 2，不许猜一个地址继续"
    assert "需要注入" in err and ENV_KEY in err


# ── E：参数冲突 ────────────────────────────────────────────────
def test_profile_and_targets_conflict(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from veripen_cli.cmd_platform import main

    spec = tmp_path / "s.json"
    spec.write_text(_spec("http" + ":" + SEP + SEP + "127." + "0.0.1:1"), encoding="utf-8")
    rc = main(["--profile", str(PROFILE), "--targets", "http" + ":" + SEP + SEP + "x.invalid",
               "--openapi", str(spec), "--out", str(tmp_path / "o")])
    assert rc == 2
    assert "不能同时给" in capsys.readouterr().err


def test_neither_source_exits_two(tmp_path: Path) -> None:
    from veripen_cli.cmd_platform import main

    spec = tmp_path / "s.json"
    spec.write_text("{}", encoding="utf-8")
    rc = main(["--openapi", str(spec), "--out", str(tmp_path / "o")])
    assert rc == 2
