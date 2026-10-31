"""B4 证据层测试：capsule 1.1 / 内容寻址 / 哈希链 / 脱敏导出（06 §2–§4）。

DoD 对应：
- 同输入两次 seal 同哈希
- 改一字节报 TAMPERED
- ``path_rendered`` 改动不影响 ``self_sha256``
- 缺 route 块不得 seal（schema 1.1 必含）
"""

from __future__ import annotations

import json

import pytest

import capsule_fixture as F
from veripen_core.evidence import (
    CapsuleIncomplete,
    CapsuleTampered,
    ChainBroken,
    assert_self_sha,
    chain_capsules,
    dumps_capsule,
    load_capsule,
    loads_capsule,
    route_block,
    save_capsule,
    seal_capsule_doc,
    strip_for_export,
    verify_chain,
    verify_self_sha,
)
from veripen_core.evidence import main as evidence_main


# ── 1 内容寻址 ──────────────────────────────────────────────────────
def test_same_input_twice_same_hash() -> None:
    a = F.sealed()
    b = F.sealed()
    assert a.chain.self_sha256 == b.chain.self_sha256
    assert dumps_capsule(a) == dumps_capsule(b), "同输入必须产出同字节"


def test_capsule_id_is_self_sha() -> None:
    cap = F.sealed()
    assert cap.capsule_id == f"sha256:{cap.chain.self_sha256}"


def test_different_content_different_hash() -> None:
    a = F.sealed()
    b = F.sealed(probe_body="other")
    assert a.chain.self_sha256 != b.chain.self_sha256


# ── 2 防篡改 ────────────────────────────────────────────────────────
def test_one_byte_change_is_tampered() -> None:
    cap = F.sealed()
    raw = dumps_capsule(cap)
    tampered = raw.replace(b'"status":200', b'"status":201', 1)
    assert tampered != raw, "夹具前提：必须真的改到字节"
    assert len(tampered) == len(raw), "只改一个字节"

    doc = loads_capsule(tampered)
    assert verify_self_sha(doc) is False
    with pytest.raises(CapsuleTampered):
        assert_self_sha(doc)


def test_tamper_then_hash_mismatch_cannot_be_hidden() -> None:
    cap = F.sealed()
    forged = cap.model_copy(update={"verdict": cap.verdict})
    forged_conclusion = forged.model_copy(
        update={"assertion": forged.assertion.model_copy(update={"observed": {"probe_status": 500}})}
    )
    assert verify_self_sha(forged_conclusion) is False


# ── 3 path_rendered 不进哈希（06 §3 v3 改动）────────────────────────
def test_path_rendered_excluded_from_hash() -> None:
    cap = F.sealed()
    steps = [
        s.model_copy(update={"request": s.request.model_copy(update={"path_rendered": "moved-elsewhere"})})
        for s in cap.steps
    ]
    moved = cap.model_copy(update={"steps": steps})
    assert verify_self_sha(moved) is True, "换环境渲染路径不应破坏证据"
    assert seal_capsule_doc(moved).chain.self_sha256 == cap.chain.self_sha256


def test_path_rendered_sha_still_anchors() -> None:
    """路径哈希仍参与：改了 path_rendered_sha256 就必须变（不是无锚点）。"""
    cap = F.sealed()
    steps = [
        s.model_copy(update={"request": s.request.model_copy(update={"path_rendered_sha256": "f" * 64})})
        for s in cap.steps
    ]
    assert verify_self_sha(cap.model_copy(update={"steps": steps})) is False


# ── 4 schema 1.1 必含 route（27 N5 / make verify-capsule）───────────
def test_route_block_required_for_v11() -> None:
    cap = F.capsule()
    naked = cap.model_copy(update={"route": None})
    with pytest.raises(CapsuleIncomplete):
        seal_capsule_doc(naked)


def test_legacy_v10_allowed_without_route() -> None:
    cap = F.capsule().model_copy(update={"route": None, "capsule_version": "1.0"})
    sealed = seal_capsule_doc(cap)
    assert verify_self_sha(sealed) is True


def test_route_block_from_routeset() -> None:
    rs = F.routeset()
    block = route_block(routeset=rs, ref=F.first_ref(rs), resolved_at=F.RESOLVED)
    assert block.routeset_sha256 == rs.routeset_sha256
    assert block.template_sha256 == rs.bindings[0].template_sha256
    with pytest.raises(CapsuleIncomplete):
        route_block(routeset=rs, ref="route://contract/not-there", resolved_at=F.RESOLVED)


# ── 5 哈希链 ────────────────────────────────────────────────────────
def test_chain_links_and_root() -> None:
    caps = chain_capsules([F.capsule(probe_body="a"), F.capsule(probe_body="b"), F.capsule(probe_body="c")])
    assert caps[0].chain.prev_sha256 == "0" * 64
    assert caps[1].chain.prev_sha256 == caps[0].chain.self_sha256
    assert caps[2].chain.prev_sha256 == caps[1].chain.self_sha256

    report = verify_chain(caps)
    assert report.ok is True
    assert report.root_sha256 == "0" * 64


def test_chain_detects_middle_tamper_with_index() -> None:
    caps = chain_capsules([F.capsule(probe_body="a"), F.capsule(probe_body="b")])
    broken = list(caps)
    broken[0] = broken[0].model_copy(update={"confidence": 0.11})

    report = verify_chain(broken)
    assert report.ok is False
    assert any(b.startswith("[0] self_sha256 不符") for b in report.breaks)
    with pytest.raises(ChainBroken):
        report.raise_if_broken()


def test_chain_detects_reordered_capsules() -> None:
    caps = chain_capsules([F.capsule(probe_body="a"), F.capsule(probe_body="b")])
    report = verify_chain([caps[1], caps[0]])
    assert report.ok is False
    assert any("链断" in b for b in report.breaks)


# ── 6 脱敏与可分享性（06 §4 / 28 断言 7）───────────────────────────
def test_headers_redacted_at_build_time() -> None:
    cap = F.capsule()
    for step in cap.steps:
        for name, value in step.request.headers.items():
            if name.lower() in ("cookie", "authorization", "x-api-key"):
                assert value == "<redacted>"
    assert "super-secret" not in dumps_capsule(cap).decode("utf-8")


def test_external_export_has_no_address() -> None:
    cap = F.sealed()
    exported = dumps_capsule(cap, external=True)
    text = exported.decode("utf-8")
    assert "203.0.113.10" not in text, "导出不得带 IP"
    assert "sha256:" not in text  # 我们是裸 hex，不是前缀形式
    for step in load_capsule_bytes(exported).steps:
        assert step.request.path_rendered is None
        assert step.peer_ip is None
        assert step.request.path_rendered_sha256, "哈希锚点必须留着"


def load_capsule_bytes(raw: bytes):  # 小工具：避免在测试里重复 import
    return loads_capsule(raw)


def test_strip_keeps_hash_anchor() -> None:
    cap = F.sealed()
    stripped = strip_for_export(cap)
    assert [s.request.path_rendered_sha256 for s in stripped.steps] == [
        s.request.path_rendered_sha256 for s in cap.steps
    ]


# ── 7 落盘：同哈希同字节（WORM 语义的本地模拟）──────────────────────
def test_save_and_load_roundtrip(tmp_path) -> None:
    cap = F.sealed()
    path = save_capsule(cap, tmp_path)
    assert path.name == f"{cap.chain.self_sha256}.json"
    again = load_capsule(path)
    assert again.chain.self_sha256 == cap.chain.self_sha256
    assert dumps_capsule(again) == dumps_capsule(cap)


def test_save_refuses_same_hash_different_bytes(tmp_path) -> None:
    cap = F.sealed()
    path = save_capsule(cap, tmp_path)
    path.write_bytes(b'{"forged":true}')
    with pytest.raises((ChainBroken, CapsuleTampered, ValueError, Exception)):
        save_capsule(cap, tmp_path)


# ── 8 CLI：verify-chain 与 --external（断网，纯本地）────────────────
def test_cli_verify_chain_and_replay(tmp_path, capsys) -> None:
    caps = chain_capsules([F.capsule(probe_body="a"), F.capsule(probe_body="b")])
    paths = [str(save_capsule(c, tmp_path)) for c in caps]

    argv = ["--verify-chain", "--offline-fixture"]
    for p in paths:
        argv += ["--capsule", p]
    assert evidence_main(argv) == 0
    out = capsys.readouterr().out
    assert "chain ok=True" in out
    assert out.count("OK") >= 2


def test_cli_reports_tampered(tmp_path, capsys) -> None:
    cap = F.sealed()
    path = save_capsule(cap, tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["confidence"] = 0.42
    path.write_text(json.dumps(raw), encoding="utf-8")

    assert evidence_main(["--capsule", str(path), "--offline-fixture"]) == 1
    assert "TAMPERED" in capsys.readouterr().out
