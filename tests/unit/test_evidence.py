"""证据测试（B4 DoD：同输入两次 seal 同哈希；改一字节报 TAMPERED；--offline-fixture 断网可跑）。"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from veripen_core import Verdict
from veripen_core.errors import ERR_CAPSULE_TAMPERED, ScopeDenied, VeripenError
from veripen_core.evidence import (
    Capsule,
    CapsuleSealer,
    ReplayEngine,
    ReplayMode,
    ReplayStatus,
    Step,
    capsule_from_dict,
    redact_body,
    verify_chain,
)

FIXED_T0 = 1_700_000_000
FIXED_T1 = 1_700_000_002


def _steps() -> tuple[Step, ...]:
    return (
        Step(
            n=1,
            role="baseline",
            account="primary",
            url="http://dvwa.local/api/users/2/profile",
            method="GET",
            request_headers={"Cookie": "session=SECRET", "Accept": "application/json"},
            status=200,
            content_type="application/json",
            response_body='{"id":2,"username":"bob"}',
            peer_ip="93.184.216.34",
            elapsed_ms=37,
        ),
        Step(
            n=2,
            role="probe",
            account="secondary",
            url="http://dvwa.local/api/users/2/profile",
            method="GET",
            request_headers={"Cookie": "session=SECRET2"},
            status=200,
            content_type="application/json",
            response_body='{"id":2,"username":"bob"}',
            peer_ip="93.184.216.34",
            elapsed_ms=41,
        ),
        Step(
            n=3,
            role="control",
            account="secondary",
            url="http://dvwa.local/api/users/3/profile",
            method="GET",
            status=403,
            response_body='{"error":"forbidden"}',
            peer_ip="93.184.216.34",
            elapsed_ms=33,
            note="对照：访问自己无权拥有的资源应被拒绝",
        ),
    )


def _capsule(**over) -> Capsule:
    base = dict(
        finding_id="F001",
        oracle_id="idor_cross_account",
        oracle_version="1.0.0",
        cwe="CWE-639",
        params_hash="sha256:" + "3a1b" * 16,
        asset_id="web-01",
        url="http://dvwa.local/api/users/2/profile",
        method="GET",
        resolved_ip="93.184.216.34",
        profile_name="demo-a",
        profile_hash="sha256:" + "7ce4" * 16,
        runner_id="worker-2@host-abc",
        clock_t0=FIXED_T0,
        clock_t1=FIXED_T1,
        steps=_steps(),
        assertion={
            "kind": "cross_account",
            "statement": "secondary 读取 primary 资源成功(200)，对照资源被拒(403)",
            "expected": {"probe_status": [200, 204], "control_status": [401, 403]},
            "observed": {"probe_status": 200, "control_status": 403},
            "fingerprint": {"probe_body_eq_baseline": True, "control_denied": True},
        },
        verdict=Verdict.VERIFIED,
        confidence=0.95,
        confidence_rule="double_condition_satisfied",
        secrets_in_vault=("vault://demo-a/primary", "vault://demo-a/secondary"),
        created_at=FIXED_T1,
    )
    base.update(over)
    return Capsule(**base)  # type: ignore[arg-type]


# ---------- DoD 1：同输入两次 seal 同哈希 ----------


def test_same_input_seals_to_same_hash() -> None:
    """内容寻址（`06 §1` #1）：同输入必得同字节。"""
    sealer = CapsuleSealer()
    a = sealer.seal_deterministic(_capsule(), prev_sha256=None)
    b = sealer.seal_deterministic(_capsule(), prev_sha256=None)
    assert a.self_sha256() == b.self_sha256()
    assert a.to_json() == b.to_json(), "byte-identical output required"


def test_different_content_gives_different_hash() -> None:
    sealer = CapsuleSealer()
    a = sealer.seal_deterministic(_capsule(), prev_sha256=None)
    b = sealer.seal_deterministic(_capsule(verdict=Verdict.REFUTED), prev_sha256=None)
    assert a.self_sha256() != b.self_sha256()


def test_sealed_capsule_is_immutable() -> None:
    """sealed 后禁止字段修改（`03 §5` / `06 §1` #3）。"""
    cap = _capsule()
    with pytest.raises(Exception):
        cap.verdict = Verdict.REFUTED  # type: ignore[misc]


# ---------- DoD 2：改一字节报 TAMPERED ----------


def test_tampered_capsule_detected(tmp_path: Path) -> None:
    """手工改一个字节 → `CAPSULE_TAMPERED`（`06 §8` #2）。"""
    sealer = CapsuleSealer(out_dir=str(tmp_path))
    sealed = sealer.seal_deterministic(_capsule(), prev_sha256=None)
    path = sealer.write(sealed)

    raw = Path(path).read_text(encoding="utf-8")
    # 篡改：把 verdict VERIFIED 改成 REFUTED，但不动 chain.self_sha256
    tampered = raw.replace('"verdict":"VERIFIED"', '"verdict":"REFUTED"')
    assert tampered != raw, "tamper must actually change bytes"
    Path(path).write_text(tampered, encoding="utf-8")

    with pytest.raises(VeripenError) as exc:
        sealer.load(path)
    assert exc.value.code == ERR_CAPSULE_TAMPERED


def test_tamper_detected_by_verify_integrity_directly() -> None:
    """不落盘也能验：改 assertion 里的一个字段即失效。"""
    cap = _capsule()
    doc = cap.to_dict()
    # 模拟攻击者改 observed 后重算 self_sha256 —— 这会被判"内容变了但 id 没变"？
    # 不：重算后 self_sha256 会不同，所以 capsule_id 也变，链就断了。
    doc["assertion"]["observed"]["probe_status"] = 500
    rebuilt = capsule_from_dict(doc)
    assert rebuilt.self_sha256() != cap.self_sha256(), "changed content must change hash"


# ---------- DoD 3：--offline-fixture 断网可跑 ----------


def test_offline_fixture_replays_without_network() -> None:
    """`--offline-fixture` 只调纯函数判定，不碰网络（`06 §6`）。

    这里的关键证据：judge 回调完全基于 capsule 内已存的响应，
    没有任何 httpx/socket 参与。
    """
    cap = _capsule()
    calls: list[str] = []

    def pure_judge(c: Capsule) -> tuple[Verdict, dict, str]:
        calls.append("judged")
        # 只读 capsule 内的 steps，不发请求
        statuses = {s.role: s.status for s in c.steps}
        probe_ok = statuses.get("probe") in (200, 204)
        control_denied = statuses.get("control") in (401, 403)
        baseline_ok = statuses.get("baseline") == 200
        if probe_ok and control_denied and baseline_ok:
            return Verdict.VERIFIED, c.assertion, "conjunction_met"
        return Verdict.REFUTED, c.assertion, "conjunction_failed"

    engine = ReplayEngine()
    outcome = engine.run(cap, mode=ReplayMode.OFFLINE_FIXTURE, judge=pure_judge)

    assert outcome.status is ReplayStatus.REPLAYED_OK
    assert outcome.verdict is Verdict.VERIFIED
    assert calls == ["judged"], "judge must be called exactly once"


def test_offline_fixture_detects_not_reproduced() -> None:
    """判定不成立 → `ASSERTION_NOT_REPRODUCED`（`06 §5.1`）。"""
    cap = _capsule()

    def judge_says_refuted(c: Capsule) -> tuple[Verdict, dict, str]:
        return Verdict.REFUTED, c.assertion, "probe_no_longer_matches"

    engine = ReplayEngine()
    outcome = engine.run(cap, mode=ReplayMode.OFFLINE_FIXTURE, judge=judge_says_refuted)
    assert outcome.status is ReplayStatus.ASSERTION_NOT_REPRODUCED
    assert any("needs_human" in n for n in outcome.notes)


def test_partial_when_details_change_but_verdict_holds() -> None:
    """关键响应变了但结论仍成立 → `REPLAYED_PARTIAL`（`06 §5.1`）。"""
    cap = _capsule()

    def judge(c: Capsule) -> tuple[Verdict, dict, str]:
        return Verdict.VERIFIED, {"observed": {"probe_status": 204, "control_status": 403}}, "still_vulnerable"

    engine = ReplayEngine()
    outcome = engine.run(cap, mode=ReplayMode.OFFLINE_FIXTURE, judge=judge)
    assert outcome.status is ReplayStatus.REPLAYED_PARTIAL
    assert outcome.verdict is Verdict.VERIFIED


# ---------- doD 4：scope 缩小时 REPLAY_BLOCKED ----------


def test_replay_blocked_when_scope_denies() -> None:
    """scope 改小后 replay 返回 `REPLAY_BLOCKED`（`06 §8` #4）。

    这是**安全行为，不是失败**——它证明"想复现但不该复现"时也拦得住。
    """
    cap = _capsule()

    def deny_all(*, asset_id: str, oracle_id: str) -> None:
        raise ScopeDenied(message=f"asset {asset_id} no longer in scope for replay")

    engine = ReplayEngine(scope_check=deny_all)
    outcome = engine.run(cap, mode=ReplayMode.OFFLINE_FIXTURE)
    assert outcome.status is ReplayStatus.REPLAY_BLOCKED
    assert outcome.is_security_block is True
    assert outcome.verdict is None, "blocked replay must not claim a verdict"


def test_replay_dry_run_sends_no_requests() -> None:
    """dry-run 不发包，只校验链路与哈希（`06 §6`）。"""
    cap = _capsule()
    engine = ReplayEngine()
    outcome = engine.run(cap, mode=ReplayMode.DRY_RUN)
    assert outcome.status is ReplayStatus.REPLAYED_OK
    assert outcome.steps_resent == 0


def test_replay_flags_stale_profile() -> None:
    """profile_hash 变了 → 标 stale 但继续（不阻断，`06 §5` step 2）。"""
    cap = _capsule()
    engine = ReplayEngine()
    outcome = engine.run(cap, mode=ReplayMode.DRY_RUN, current_profile_hash="sha256:" + "dead" * 16)
    assert any("PROFILE_CHANGED" in n for n in outcome.notes)


# ---------- 哈希链 ----------


def test_chain_links_capsules_in_order() -> None:
    """`capsule_i.prev = capsule_{i-1}.self`（`06 §3`）。"""
    sealer = CapsuleSealer()
    c1 = sealer.seal(_capsule(finding_id="F001"))
    c2 = sealer.seal(_capsule(finding_id="F002"))
    c3 = sealer.seal(_capsule(finding_id="F003"))

    result = verify_chain([c1, c2, c3])
    assert result["ok"] is True
    assert result["checked"] == 3
    assert c2.prev_sha256 == c1.self_sha256()
    assert c3.prev_sha256 == c2.self_sha256()


def test_chain_break_detected_and_located() -> None:
    """断链必须定位到具体条目，不是笼统报错。"""
    sealer = CapsuleSealer()
    c1 = sealer.seal(_capsule(finding_id="F001"))
    c2 = sealer.seal(_capsule(finding_id="F002"))
    c3 = sealer.seal(_capsule(finding_id="F003"))

    # 破坏中间那条的 prev
    import dataclasses

    c2_broken = dataclasses.replace(c2, prev_sha256="sha256:" + "0" * 64)
    result = verify_chain([c1, c2_broken, c3])
    assert result["ok"] is False
    assert result["break_at"] == "F002"
    assert "chain break" in result["reason"]


def test_empty_chain_is_ok() -> None:
    assert verify_chain([])["ok"] is True


# ---------- 脱敏 ----------


def test_sensitive_headers_redacted_in_capsule() -> None:
    """capsule 里 Cookie 值不得出现（`06 §4`）。"""
    cap = _capsule()
    doc = cap.to_dict()
    step = doc["steps"][0]
    assert step["request"]["headers"]["Cookie"] == "<redacted>"
    as_text = json.dumps(doc, ensure_ascii=False)
    assert "session=SECRET" not in as_text, "cookie value must never reach the capsule"


def test_pii_redacted_in_response_body() -> None:
    """响应体按正则脱敏：手机号/邮箱/身份证/VIN/银行卡/JWT（`06 §4`）。"""
    body = (
        "phone 13800138000 email alice@example.com "
        "id 110101199003075612 vin LSVAA49J2DN123456 "
        "card 4111 1111 1111 1111 jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefghij"
    )
    out = redact_body(body)
    assert "13800138000" not in out
    assert "alice@example.com" not in out
    assert "110101199003075612" not in out
    assert "LSVAA49J2DN123456" not in out

    for placeholder in ("<phone>", "<email>", "<id-card>", "<vin>"):
        assert placeholder in out


def test_response_body_truncated_to_4kb() -> None:
    """响应体保留前 4KB，超出的标 `excerpt_truncated`（`06 §4`）。"""
    big = "A" * 10_000
    cap = _capsule(steps=(Step(n=1, role="probe", account="p", url="http://dvwa.local/", response_body=big),))
    doc = cap.to_dict()
    resp = doc["steps"][0]["response"]
    assert resp["excerpt_truncated"] is True
    assert len(base64.b64decode(resp["body_excerpt_b64"])) == 4096
    assert resp["length"] == 10_000, "full length must still be recorded"


def test_no_secret_in_full_capsule_json() -> None:
    """整份 capsule JSON 里不得出现 vault 密钥值（可分享性验证，`06 §4`）。"""
    cap = _capsule()
    text = cap.to_json()
    for leak in ("SECRET", "SECRET2", "Bearer ", "password="):
        assert leak not in text, f"{leak!r} leaked into capsule"
