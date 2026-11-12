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
    assert report.genesis_prev_sha256 == "0" * 64


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


# ── B293：链校验要有产品入口（bench 一直提示的那个命令不存在）────────
def test_replay_chain_verifies_a_directory(tmp_path: Path) -> None:
    """`veripen evidence --verify-chain` 是条不存在的命令；能力在 core 里，缺的是入口。"""
    from veripen_cli.cmd_replay import main as replay_main
    from veripen_core.evidence import save_capsule, seal_capsule_doc

    caps_dir = tmp_path / "capsules"
    caps_dir.mkdir()
    prev = "0" * 64
    for _ in range(2):
        cap = seal_capsule_doc(F.capsule(), prev_sha256=prev)
        save_capsule(cap, caps_dir)
        prev = cap.chain.self_sha256
    assert replay_main(["--chain", str(caps_dir)]) == 0


def test_replay_chain_refuses_an_empty_target(tmp_path: Path) -> None:
    from veripen_cli.cmd_replay import main as replay_main

    empty = tmp_path / "none"
    empty.mkdir()
    assert replay_main(["--chain", str(empty)]) == 1


# ── B320：清单校验要有产品入口（以前只能手工 sha256sum）────────────
def test_replay_manifest_verifies_a_clean_delivery(tmp_path: Path) -> None:
    from veripen.report.lint import build_manifest, dumps_artifact
    from veripen_cli.cmd_replay import main as replay_main

    md, sarif, js = "# 报告\n", {"version": "2.1.0"}, {"findings": []}
    (tmp_path / "report.md").write_text(md, encoding="utf-8", newline=chr(10))
    (tmp_path / "report.sarif").write_text(dumps_artifact(sarif), encoding="utf-8", newline=chr(10))
    (tmp_path / "findings.json").write_text(dumps_artifact(js), encoding="utf-8", newline=chr(10))
    (tmp_path / "report.manifest.json").write_text(
        json.dumps(build_manifest(markdown=md, sarif=sarif, js=js), ensure_ascii=False),
        encoding="utf-8", newline=chr(10))
    assert replay_main(["--manifest", str(tmp_path)]) == 0


def test_replay_manifest_catches_a_tampered_file(tmp_path: Path) -> None:
    from veripen.report.lint import build_manifest, dumps_artifact
    from veripen_cli.cmd_replay import main as replay_main

    md, sarif, js = "# 报告\n", {"version": "2.1.0"}, {"findings": []}
    (tmp_path / "report.md").write_text(md + "手改\n", encoding="utf-8", newline=chr(10))
    (tmp_path / "report.sarif").write_text(dumps_artifact(sarif), encoding="utf-8", newline=chr(10))
    (tmp_path / "findings.json").write_text(dumps_artifact(js), encoding="utf-8", newline=chr(10))
    (tmp_path / "report.manifest.json").write_text(
        json.dumps(build_manifest(markdown=md, sarif=sarif, js=js), ensure_ascii=False),
        encoding="utf-8", newline=chr(10))
    assert replay_main(["--manifest", str(tmp_path)]) == 1


def test_replay_manifest_refuses_a_manifest_without_files(tmp_path: Path) -> None:
    from veripen_cli.cmd_replay import main as replay_main

    (tmp_path / "report.manifest.json").write_text(
        json.dumps({"version": 1, "sections": {}}), encoding="utf-8")
    assert replay_main(["--manifest", str(tmp_path)]) == 1


# ── B321：清单自哈希——改清单里的哈希值也要被发现 ────────────────
def test_manifest_self_hash_catches_an_edited_hash(tmp_path: Path) -> None:
    from veripen.report.lint import build_manifest, dumps_artifact
    from veripen_cli.cmd_replay import main as replay_main

    md, sarif, js = "# 报告\n", {"version": "2.1.0"}, {"findings": []}
    for name, text in (("report.md", md), ("report.sarif", dumps_artifact(sarif)),
                       ("findings.json", dumps_artifact(js))):
        (tmp_path / name).write_text(text, encoding="utf-8", newline=chr(10))
    manifest = build_manifest(markdown=md, sarif=sarif, js=js)
    path = tmp_path / "report.manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8", newline=chr(10))
    assert replay_main(["--manifest", str(tmp_path)]) == 0

    tampered = dict(manifest)
    tampered["files"] = {**manifest["files"], "report.md": "0" * 64}
    path.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8", newline=chr(10))
    assert replay_main(["--manifest", str(tmp_path)]) == 1


def test_hunt_and_platform_manifests_carry_a_self_hash() -> None:
    import inspect

    from veripen_cli import cmd_hunt, cmd_platform

    assert "manifest_sha256" in inspect.getsource(cmd_hunt.main)
    assert "manifest_sha256" in inspect.getsource(cmd_platform.main)


# ── B338：造 capsule 必须走校验版 route_block ─────────────────────
def test_build_capsule_refuses_a_ref_not_in_the_routeset() -> None:
    """"证据不得凭空生成 route 块"：以前 build_capsule 直接把 route_ref 与 binding 拼上，
    两者对不上也照样出证。"""
    import pytest

    import capsule_fixture as F
    from veripen.bench.capsule import build_capsule
    from veripen_core.evidence import CapsuleIncomplete

    rs = F.routeset()
    binding = rs.bindings[0]
    with pytest.raises(CapsuleIncomplete):
        build_capsule(
            case_id="c", oracle_id="idor_cross_account", oracle_version="1",
            verdict=F.Verdict.VERIFIED, judgement=type("J", (), {"assertion": {}})(),
            pairs=(), baseline_params={}, routeset=rs,
            route_ref="route://contract/" + "f" * 16, binding=binding,
        )


# ── B347：dry 模式的 ROUTE_UNRESOLVED 是设计使然，要说清 ────────────
def test_dry_mode_explains_the_unresolved_route(tmp_path: Path, capsys) -> None:  # noqa: ANN001
    """dry 刻意不注入 RouteSet，路由门必然 UNRESOLVED——不说明会被读成"路由没了"。"""
    import capsule_fixture as F
    from veripen_cli.cmd_replay import main as replay_main
    from veripen_core.evidence import save_capsule, seal_capsule_doc

    caps = tmp_path / "c"
    caps.mkdir()
    save_capsule(seal_capsule_doc(F.capsule(), prev_sha256="0" * 64), caps)
    path = next(caps.glob("*.json"))
    rc = replay_main(["--capsule", str(path), "--mode", "dry", "--out", str(tmp_path / "o")])
    out = capsys.readouterr().out
    assert rc in (0, 1), rc
    if "ROUTE_UNRESOLVED" in out:
        assert "刻意给空" in out, out


# ── B349：--chain 要按血缘排序，且篡改不抛 traceback ──────────────
def _two_capsule_chain(tmp_path: Path) -> Path:
    import capsule_fixture as F
    from veripen_core.evidence import save_capsule, seal_capsule_doc

    caps = tmp_path / "caps"
    caps.mkdir()
    prev = "0" * 64
    for _ in range(2):
        cap = seal_capsule_doc(F.capsule(), prev_sha256=prev)
        save_capsule(cap, caps)
        prev = cap.chain.self_sha256
    return caps


def test_chain_is_ordered_by_lineage_not_filename(tmp_path: Path) -> None:
    """文件名字典序不是链序——直接排会把干净链判成断点（实测过）。"""
    from veripen_cli.cmd_replay import main as replay_main

    assert replay_main(["--chain", str(_two_capsule_chain(tmp_path))]) == 0


def test_chain_reports_tampering_without_a_traceback(tmp_path: Path, capsys) -> None:  # noqa: ANN001
    import json as _json

    from veripen_cli.cmd_replay import main as replay_main

    caps = _two_capsule_chain(tmp_path)
    first = sorted(caps.glob("*.json"))[0]
    doc = _json.loads(first.read_text(encoding="utf-8"))
    doc["finding_id"] = "tampered"
    first.write_text(_json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    rc = replay_main(["--chain", str(caps)])
    err = capsys.readouterr().err
    assert rc == 1
    assert "TAMPERED" in err and "Traceback" not in err, err
