"""B4 手工演示脚本：3 capsule → 链 → 篡改 → 四态（`06 §8` 验收四条）。

用法：python scripts/demo_b4_evidence.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from veripen_core import Verdict  # noqa: E402
from veripen_core.evidence import Capsule, CapsuleSealer, Step  # noqa: E402


def build_capsule(i: int) -> Capsule:
    return Capsule(
        finding_id=f"F{i:03d}",
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
                 request_headers={"Cookie": "session=SECRET2"}, status=200,
                 response_body='{"id":2}', peer_ip="93.184.216.34", elapsed_ms=41),
            Step(3, "control", "secondary", "http://dvwa.local/api/users/3/profile",
                 status=403, response_body='{"error":"forbidden"}',
                 peer_ip="93.184.216.34", elapsed_ms=33),
        ),
        assertion={"kind": "cross_account", "observed": {"probe_status": 200, "control_status": 403}},
        verdict=Verdict.VERIFIED,
        confidence=0.95,
        confidence_rule="double_condition_satisfied",
        secrets_in_vault=("vault://demo-a/primary", "vault://demo-a/secondary"),
        created_at=1_700_000_002,
    )


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "veripen_cli.main", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=ROOT,
    )


def main() -> int:
    out = ROOT / "out" / "capsules"
    out.mkdir(parents=True, exist_ok=True)
    sealer = CapsuleSealer(out_dir=str(out))

    paths: list[str] = []
    for i in range(1, 4):
        cap = sealer.seal(build_capsule(i))
        paths.append(sealer.write(cap))

    print("=" * 66)
    print("[1] 3 条发现 → 3 个 capsule → 哈希链完整")
    print("=" * 66)
    r = run("verify-chain", *paths, "--json")
    print(r.stdout.strip())

    print("\n" + "=" * 66)
    print("[2] replay --dry-run（不发包，只校验链路与哈希）")
    print("=" * 66)
    r = run("replay", paths[0], "--dry-run", "--json")
    d = json.loads(r.stdout)
    print(f"  status={d['status']}  steps_resent={d['steps_resent']}")
    for n in d["notes"]:
        print(f"  note: {n}")

    print("\n" + "=" * 66)
    print("[3] replay --offline-fixture（断网可跑，只调纯函数）")
    print("=" * 66)
    r = run("replay", paths[1], "--offline-fixture", "--json")
    d = json.loads(r.stdout)
    print(f"  status={d['status']}  verdict={d['verdict']}")

    print("\n" + "=" * 66)
    print("[4] 手工改一个字节 → 必须报 CAPSULE_TAMPERED")
    print("=" * 66)
    p = Path(paths[2])
    raw = p.read_text(encoding="utf-8")
    tampered = raw.replace('"verdict":"VERIFIED"', '"verdict":"REFUTED"')
    p.write_text(tampered, encoding="utf-8")
    r = run("replay", paths[2], "--dry-run", "--json")
    d = json.loads(r.stdout)
    print(f"  exit={r.returncode}  code={d.get('code')}  message={d.get('message')}")

    print("\n" + "=" * 66)
    print("[5] 可分享性：capsule 里没有任何凭据值")
    print("=" * 66)
    leaks = []
    for path in paths:
        text = Path(path).read_text(encoding="utf-8")
        for secret in ("session=SECRET", "session=SECRET2"):
            if secret in text:
                leaks.append(f"{path}: {secret}")
    print(f"  泄漏检查: {'FAIL ' + str(leaks) if leaks else 'PASS — 0 处凭据值'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
