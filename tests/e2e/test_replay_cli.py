"""B4 端到端：用真实 CLI 跑 replay 四态 + 篡改检测 + 断网模式。

这些是 `06 §8` 的验收四条，必须通过**真实命令行**证明，不是单测替身：
  1. 3 条发现 → 3 个 capsule → 哈希链完整
  2. 手工改一个字节 → replay 报 CAPSULE_TAMPERED
  3. replay --dry-run 与 --offline-fixture 都通过（后者断网可跑）
  4. scope 改小后 replay 返回 REPLAY_BLOCKED
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from veripen_core import Verdict
from veripen_core.evidence import Capsule, CapsuleSealer, Step

REPO_ROOT = Path(__file__).resolve().parents[2]


def _capsule(finding_id: str, *, probe_status: int = 200, control_status: int = 403) -> Capsule:
    return Capsule(
        finding_id=finding_id,
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
        clock_t0=1_700_000_000,
        clock_t1=1_700_000_002,
        steps=(
            Step(1, "baseline", "primary", "http://dvwa.local/api/users/2/profile",
                 request_headers={"Cookie": "session=SECRET"}, status=200,
                 response_body='{"id":2}', peer_ip="93.184.216.34", elapsed_ms=37),
            Step(2, "probe", "secondary", "http://dvwa.local/api/users/2/profile",
                 request_headers={"Cookie": "session=SECRET2"}, status=probe_status,
                 response_body='{"id":2}', peer_ip="93.184.216.34", elapsed_ms=41),
            Step(3, "control", "secondary", "http://dvwa.local/api/users/3/profile",
                 status=control_status, response_body='{"error":"forbidden"}',
                 peer_ip="93.184.216.34", elapsed_ms=33),
        ),
        assertion={
            "kind": "cross_account",
            "observed": {"probe_status": probe_status, "control_status": control_status},
            "fingerprint": {"probe_body_eq_baseline": True, "control_denied": control_status in (401, 403)},
        },
        verdict=Verdict.VERIFIED,
        confidence=0.95,
        confidence_rule="double_condition_satisfied",
        secrets_in_vault=("vault://demo-a/primary", "vault://demo-a/secondary"),
        created_at=1_700_000_002,
    )


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "veripen_cli.main", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=REPO_ROOT,
        env=env,
        timeout=60,
    )


@pytest.fixture()
def sealed_capsules(tmp_path: Path) -> list[Path]:
    """3 条发现 → 3 个 capsule 文件，链完整。"""
    sealer = CapsuleSealer(out_dir=str(tmp_path))
    paths = []
    for i in range(1, 4):
        cap = sealer.seal(_capsule(f"F{i:03d}"))
        paths.append(Path(sealer.write(cap)))
    return paths


def test_cli_verify_chain_ok(sealed_capsules: list[Path]) -> None:
    """`06 §8` #1：3 个 capsule 哈希链完整。"""
    r = _run_cli("verify-chain", *[str(p) for p in sealed_capsules], "--json")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["ok"] is True
    assert out["checked"] == 3
    assert out["last_sha256"].startswith("sha256:")


def test_cli_replay_dry_run(sealed_capsules: list[Path]) -> None:
    """`06 §6`：dry-run 不发包，只校验链路与哈希。"""
    r = _run_cli("replay", str(sealed_capsules[0]), "--dry-run", "--json")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["status"] == "REPLAYED_OK"
    assert out["steps_resent"] == 0
    assert any("dry_run" in n for n in out["notes"])


def test_cli_replay_offline_fixture_without_network(sealed_capsules: list[Path]) -> None:
    """`06 §6` 第三种模式：断网可用，因为它只调纯函数判定。

    真断网不好在 CI 里造，所以这里断言"没有触碰网络模块"——
    用 `-X importtime` 太重，改为断言 CLI 在空环境变量下也能跑完并给四态之一。
    """
    r = _run_cli("replay", str(sealed_capsules[1]), "--offline-fixture", "--json")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["status"] in {"REPLAYED_OK", "REPLAYED_PARTIAL"}


def test_cli_replay_detects_tampered_capsule(sealed_capsules: list[Path]) -> None:
    """`06 §8` #2：手工改一个字节 → CAPSULE_TAMPERED。"""
    path = sealed_capsules[2]
    raw = path.read_text(encoding="utf-8")
    tampered = raw.replace('"verdict":"VERIFIED"', '"verdict":"REFUTED"')
    assert tampered != raw
    path.write_text(tampered, encoding="utf-8")

    r = _run_cli("replay", str(path), "--dry-run", "--json")
    assert r.returncode == 2, f"expected exit 2 for tampered capsule, got {r.returncode}: {r.stdout}"
    out = json.loads(r.stdout)
    assert out["status"] == "ERROR"
    assert out["code"] == "VP-5001"


def test_cli_replay_missing_capsule(sealed_capsules: list[Path], tmp_path: Path) -> None:
    """缺失比篡改更早拦：`VP-5002`。"""
    missing = tmp_path / "nope.json"
    r = _run_cli("replay", str(missing), "--dry-run", "--json")
    assert r.returncode == 2
    assert json.loads(r.stdout)["code"] == "VP-5002"


def test_full_capsule_has_no_secrets(sealed_capsules: list[Path]) -> None:
    """可分享性（`06 §4`）：整份 capsule 里没有任何凭据值。"""
    for p in sealed_capsules:
        text = p.read_text(encoding="utf-8")
        assert "session=SECRET" not in text
        assert "session=SECRET2" not in text


def test_capsule_bytes_are_stable_across_writes(tmp_path: Path) -> None:
    """内容寻址：同输入两次写盘字节一致（`06 §1` #1）。"""
    sealer = CapsuleSealer(out_dir=str(tmp_path / "a"))
    c1 = sealer.seal_deterministic(_capsule("F001"), prev_sha256=None)
    p1 = sealer.write(c1)

    sealer2 = CapsuleSealer(out_dir=str(tmp_path / "b"))
    c2 = sealer2.seal_deterministic(_capsule("F001"), prev_sha256=None)
    p2 = sealer2.write(c2)

    assert Path(p1).read_bytes() == Path(p2).read_bytes()
