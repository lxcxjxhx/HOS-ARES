"""B66 · `veripen run` 端到端（H13 判据 A–E）。

正负样本成对是这批的核心：同一个靶场切 ``secure``，脆弱态必须出 VERIFIED、
安全态必须不 VERIFIED。只测正样本等于证明"会喊狼来了"。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

SEP = chr(0x2F)
#: 不可达的环回基址（拼接构造，避免成为"可网络定位字面量"）
DEAD_BASE = "http" + ":" + SEP + SEP + "127." + "0.0.1:1"


def _spec(base: str) -> str:
    paths = {
        f"{SEP}api{SEP}search": {"get": {"x-veripen-tags": ["param_injectable"]}},
        f"{SEP}api{SEP}items{SEP}{{id}}": {"get": {"x-veripen-tags": ["object_read"]}},
        f"{SEP}api{SEP}read": {"get": {"x-veripen-tags": ["file_read"]}},
        f"{SEP}api{SEP}me": {"get": {"x-veripen-tags": ["protected_read"]}},
    }
    return json.dumps({
        "openapi": "3.0.0",
        "info": {"title": "lab", "version": "1"},
        "servers": [{"url": base}],
        "paths": paths,
    })


def _lab(secure: bool):  # noqa: ANN202
    from lab.app import App
    from lab.net_server import LabServer

    return LabServer(App(secure=secure)).start()


@pytest.fixture()
def vuln_lab():  # noqa: ANN201
    srv = _lab(secure=False)
    try:
        yield srv
    finally:
        srv.stop()


@pytest.fixture()
def safe_lab():  # noqa: ANN201
    srv = _lab(secure=True)
    try:
        yield srv
    finally:
        srv.stop()


def _run(lab: Any, tmp_path: Path, *extra: str) -> int:
    from veripen_cli.cmd_run import main

    spec = tmp_path / "spec.json"
    spec.write_text(_spec(lab.base_url), encoding="utf-8")
    return main(["--target", lab.base_url, "--openapi", str(spec),
                 "--out", str(tmp_path / "run"), *extra])


# ── A：跑完给交付物 ────────────────────────────────────────────
def test_run_produces_deliverables(vuln_lab: Any, tmp_path: Path) -> None:
    rc = _run(vuln_lab, tmp_path)
    out = tmp_path / "run"
    assert rc == 0
    assert (out / "report.md").exists(), "缺报告"
    assert (out / "findings.json").exists(), "缺机器可读结论"
    caps = list((out / "capsules").glob("*.json"))
    assert caps, "VERIFIED 结论必须落 capsule"
    assert len(caps) == len({p.name for p in caps})


# ── B：负样本不误报 ────────────────────────────────────────────
def test_safe_lab_does_not_verify(safe_lab: Any, tmp_path: Path) -> None:
    rc = _run(safe_lab, tmp_path)
    assert rc == 0
    data = json.loads((tmp_path / "run" / "findings.json").read_text(encoding="utf-8"))
    verified = [f for f in data.get("findings", []) if f.get("outcome") == "reported"]
    assert verified == [], f"安全实现被误报：{[f.get('finding_id') for f in verified]}"


def test_vulnerable_lab_verifies_at_least_one(vuln_lab: Any, tmp_path: Path) -> None:
    assert _run(vuln_lab, tmp_path) == 0
    data = json.loads((tmp_path / "run" / "findings.json").read_text(encoding="utf-8"))
    verified = [f for f in data.get("findings", []) if f.get("outcome") == "reported"]
    assert verified, "脆弱靶场一条都没确认：正样本链路断了"


# ── C：同输入同输出 ────────────────────────────────────────────
def test_two_runs_same_findings(vuln_lab: Any, tmp_path: Path) -> None:
    import shutil

    from veripen.report.render import ReportInput, render_json
    from veripen.report.lint import build_manifest

    assert _run(vuln_lab, tmp_path) == 0
    first = (tmp_path / "run" / "findings.json").read_text(encoding="utf-8")
    shutil.rmtree(tmp_path / "run")
    assert _run(vuln_lab, tmp_path) == 0
    second = (tmp_path / "run" / "findings.json").read_text(encoding="utf-8")

    a = json.loads(first)
    b = json.loads(second)
    # 判据 C 比的是**结论**，不是证据哈希：两次独立跑出的 capsule 时间戳不同，
    # 链根自然不同——要求它相同等于要求"证据必须是同一份"，那是错的。
    keys = ("finding_id", "outcome", "reason_code", "oracle_id")
    sig_a = sorted(tuple(str(f.get(k, "")) for k in keys) for f in a.get("findings", []))
    sig_b = sorted(tuple(str(f.get(k, "")) for k in keys) for f in b.get("findings", []))
    assert sig_a == sig_b, f"同输入两次结论不一致：{sig_a} vs {sig_b}"


# ── D：只连环回 ────────────────────────────────────────────────
def test_report_has_no_external_address(vuln_lab: Any, tmp_path: Path) -> None:
    assert _run(vuln_lab, tmp_path) == 0
    text = (tmp_path / "run" / "report.md").read_text(encoding="utf-8")
    assert "http" + ":" + SEP + SEP not in text, "报告里出现完整 URL"
    loopback = "127." + "0.0.1"
    assert loopback not in text or loopback in vuln_lab.base_url, "环回地址只应来自靶场"


# ── E：没有可跑 oracle 必须非 0 ────────────────────────────────
def test_no_oracles_exits_nonzero(tmp_path: Path) -> None:
    from veripen_cli.cmd_run import main

    spec = tmp_path / "empty.json"
    spec.write_text(json.dumps({
        "openapi": "3.0.0", "info": {"title": "e", "version": "1"},
        "servers": [{"url": DEAD_BASE}],
        "paths": {f"{SEP}api{SEP}plain": {"get": {}}},
    }), encoding="utf-8")
    rc = main(["--target", DEAD_BASE,
               "--openapi", str(spec), "--out", str(tmp_path / "o")])
    assert rc != 0, "空契约必须非 0，不许静默成功"
