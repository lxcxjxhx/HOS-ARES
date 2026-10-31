"""B4 验收（06 §8 一张表跑完）+ ``--offline-fixture`` 的断网自证。

断网自证不是"我没连网"，而是：**把 socket 焊死**，整条链路仍然给出六态。
"""

from __future__ import annotations

import json
import re
import socket

import capsule_fixture as F
import pytest

import veripen_core.evidence as E
from veripen_core.evidence import (
    ReplayEnv,
    ReplayMode,
    main as evidence_main,
    replay,
    save_capsule,
    verify_chain,
)
from veripen_core.models import ReplayStatus
from veripen_core.store import Store


@pytest.fixture()
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **kw: object) -> None:
        raise AssertionError("offline-fixture 期间不得创建 socket")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)


# ── 1 三条发现 → 三个 capsule → 链完整、都含 route 块 ───────────────
def test_three_findings_chain_complete(tmp_path) -> None:
    caps = F.chain_capsules([F.capsule(probe_body=f"y{i}") for i in range(3)])
    for cap in caps:
        assert cap.route is not None, "1.1 必含 route 块"
        assert cap.route.template_sha256
        assert cap.route.routeset_sha256 == F.routeset().routeset_sha256

    paths = [save_capsule(c, tmp_path) for c in caps]
    report = verify_chain([E.load_capsule(p) for p in paths])
    assert report.ok is True
    assert report.root_sha256 == "0" * 64


# ── 2 改一个字节 → TAMPERED（CLI 也要报）─────────────────────────────
def test_tampered_via_cli(tmp_path, capsys) -> None:
    path = save_capsule(F.sealed(), tmp_path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["steps"][0]["response"]["status"] = 500
    path.write_text(json.dumps(doc), encoding="utf-8")

    assert evidence_main(["--capsule", str(path), "--offline-fixture"]) == 1
    assert "TAMPERED" in capsys.readouterr().out


# ── 3 断网可跑（socket 焊死，六态照出）──────────────────────────────
def test_offline_fixture_runs_without_network(tmp_path, no_network, capsys) -> None:
    cap = F.sealed()
    path = save_capsule(cap, tmp_path)
    assert evidence_main(["--capsule", str(path), "--offline-fixture"]) == 0
    out = capsys.readouterr().out
    assert "OK" in out and "reason=reproduced" in out


def test_offline_fixture_refuses_transport() -> None:
    env = ReplayEnv(now=F.NOW, mode=ReplayMode.OFFLINE)
    with pytest.raises(E.ReplayBlocked):
        replay(F.sealed(), env, object())  # type: ignore[arg-type]


def test_offline_never_touches_routeset_binding(monkeypatch, no_network) -> None:
    """离线模式不解析路由：给一个'解析不出来'的 routeset 也照样 OK。"""
    env = ReplayEnv(now=F.NOW, mode=ReplayMode.OFFLINE, routeset=F.drop_binding(F.routeset()))
    assert replay(F.sealed(), env).state is ReplayStatus.OK


# ── 4 缩 scope → BLOCKED（该拦的时候拦得住）──────────────────────────
def test_shrink_scope_blocks(tmp_path, capsys) -> None:
    cap = F.sealed()
    env = ReplayEnv(now=F.NOW, mode=ReplayMode.DRY, scope_in=frozenset({"other-asset"}))
    result = replay(cap, env, None)
    assert result.state is ReplayStatus.BLOCKED
    assert result.would_send == ()


# ── 5 删绑定 → ROUTE_UNRESOLVED ────────────────────────────────────
def test_delete_binding_unresolved() -> None:
    env = ReplayEnv(now=F.NOW, mode=ReplayMode.DRY, routeset=F.drop_binding(F.routeset()))
    assert replay(F.sealed(), env).state is ReplayStatus.ROUTE_UNRESOLVED


# ── 6 改模板 → ROUTE_DRIFT ────────────────────────────────────────
def test_change_template_drift() -> None:
    rs = F.routeset()
    drifted = F.clone_with_template(rs, F.first_ref(rs), F.TEMPLATES["admin"], "c" * 64)
    env = ReplayEnv(now=F.NOW, mode=ReplayMode.DRY, routeset=drifted)
    assert replay(F.sealed(), env).state is ReplayStatus.ROUTE_DRIFT


# ── 7 导出零地址（06 §8 第 7 条）────────────────────────────────────
URL_SCHEME_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://")


def test_export_has_zero_address() -> None:
    cap = F.sealed()
    text = E.dumps_capsule(cap, external=True).decode("utf-8")
    assert "203.0.113.10" not in text
    schemes = {m.group(0) for m in URL_SCHEME_RE.finditer(text)}
    assert schemes <= {"route://", "vault://"}, f"导出出现可定位 scheme: {sorted(schemes)}"
    # 语义仍可读：哈希锚点在
    assert cap.steps[0].request.path_rendered_sha256 in text


# ── 8 replay 结果回写（06 §5 步骤 7），capsule 文件不动 ─────────────
def test_replay_status_written_back(tmp_path) -> None:
    store = Store(tmp_path / "veripen.db").init()
    try:
        cap = F.sealed()
        path = save_capsule(cap, tmp_path / "capsules")
        from veripen_core.models import Capsule as CoreCapsule, Verdict

        core = CoreCapsule(
            finding_id=cap.finding_id,
            oracle_id=cap.oracle.id,
            oracle_version=cap.oracle.version,
            verdict=Verdict.VERIFIED,
            assertion={"kind": cap.assertion.kind},
            routeset_sha256=cap.route.routeset_sha256 if cap.route else None,
            route_ref=cap.route.ref if cap.route else None,
            prev_sha256=cap.chain.prev_sha256,
            sealed_at=1,
        )
        from veripen_core.models import seal_capsule

        cid = store.record_capsule(seal_capsule(core), str(path))
        assert store.get_capsule(cid)["replay_status"] == "NEVER"

        store.record_replay(cid, ReplayStatus.ROUTE_DRIFT, at=1234)
        row = store.get_capsule(cid)
        assert row["replay_status"] == "ROUTE_DRIFT"
        assert row["replay_at"] == 1234
        assert path.read_bytes() == E.dumps_capsule(cap), "回写不得改动 capsule 文件"
    finally:
        store.close()
