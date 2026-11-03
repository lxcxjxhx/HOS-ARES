#!/usr/bin/env python3
"""B12.5 bench-abi：单/多 agent 的 findings 集合必须相同（21 §3.5.4 第 2、3 条）。

做法：把 bench 的 44 例转成 TaskSuggestion，分别用串行与并行各跑一遍，
比对 findings 指纹与请求数。**多 agent 不许改变结论，也不许多发请求。**

用法：`python scripts/b12/bench_abi.py [--workers 2]`
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests", ROOT / "tests" / "unit"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from veripen.bench import load_cases  # noqa: E402
from veripen_core.agents import (  # noqa: E402
    AgentError,
    FindingRecord,
    TaskSuggestion,
    assert_abi_equal,
    orchestrate,
)
from veripen_core.outcome import Outcome  # noqa: E402


def _run(s: TaskSuggestion) -> FindingRecord:
    """判定只由 (oracle, route) 决定，与并发顺序无关——这是 ABI 比对的前提。"""
    verdict = "VERIFIED" if s.oracle_id.startswith(("sqli", "idor", "ssrf", "jwt")) else "REFUTED"
    return FindingRecord(
        oracle_id=s.oracle_id,
        route_ref=s.route_ref,
        verdict=verdict,
        capsule_id=("sha256:" + "0" * 64) if verdict == "VERIFIED" else None,
        outcome=Outcome.REPORTED,
    )


def _to_suggestions() -> list[tuple[str, str]]:
    cases = load_cases(ROOT / "tests" / "bench" / "cases.yaml")
    # route_ref 用 case_id 派生：同一用例串行与并行的 route 必须逐字相同
    return [(c.oracle, f"route://observed/{c.case_id}") for c in cases]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--out", default="out/b12")
    args = ap.parse_args(argv)

    pairs = _to_suggestions()
    single = orchestrate(pairs, run=_run, parallel=False)
    multi = orchestrate(pairs, run=_run, parallel=True, max_workers=args.workers)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "bench.single.json").write_text(
        json.dumps(
            {"findings_sha256": single.fingerprint, "stats": single.stats.as_dict(),
             "keys": sorted(f.key for f in single.findings)},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    (out / "bench.multi.json").write_text(
        json.dumps(
            {"findings_sha256": multi.fingerprint, "stats": multi.stats.as_dict(),
             "keys": sorted(f.key for f in multi.findings)},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(f"cases: {len(pairs)}  single_sha: {single.fingerprint[:12]}"
          f"  multi_sha: {multi.fingerprint[:12]}")
    print(f"requests: single={single.stats.requests} multi={multi.stats.requests}")
    try:
        assert_abi_equal(single, multi)
    except AgentError as exc:
        print(f"BENCH-ABI FAIL: {exc}")
        return 1
    print("bench-abi: OK（findings 集合相同，请求数相同）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
