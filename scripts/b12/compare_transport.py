"""对比：同一批 44 例在「进程内 WSGI」与「真网络」下的判定差异。

**这是整条改造线的验收点。** 差异为空 → 传输替换是等价变更；差异非空 →
每一处都必须能解释，不能归类为"抖动"。

⚠️ 这个脚本曾经在 ``run_bench`` 切到真网络之后**自己和自己比**：
它把 ``run_bench.run_case``（已改真网络）当作"WSGI 侧"，把
``test_bench_on_network._run_on_network``（也是真网络）当作"net 侧"，
于是 ``diff_count`` 恒为 0——一个永远绿的验收点，比没有验收点更糟。

现在两侧都走**各自独立**的实现：
- 进程内：``WsgiTransport`` + ``lab.routes`` 的相对绑定（无 socket）；
- 真网络：``NetTransport`` + ``SymbolSource(base=L…)`` 的绝对绑定（真 socket）。

两侧共用 ``veripen.bench.faces`` 的面映射与 ``veripen.bench`` 的用例集，
其余部分不共享——**共享的部分越少，这条对比才越有意义**。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests", ROOT / "tests" / "unit", ROOT / "tests" / "integration"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from veripen.bench import load_cases  # noqa: E402
from veripen.bench.faces import extra_tags  # noqa: E402

SYMBOLS = {
    "OOB": "oob-SAMPLE",
    "OOB_URL": "https" + ":" + "//" + "oob-SAMPLE",
    "DEAD_HOST": "none-SAMPLE",
    "STRANGER": "secondary",
    "OWNER": "primary",
    "SAFE_TARGET": "account/settings",
    "SAFE_FILE": "manual.txt",
    "SYSTEM_FILE": "etc/passwd",
    "SITE_HOST": "lab",
    "SITE_ORIGIN": "https" + ":" + "//" + "site.invalid",
}


def _in_process(case) -> tuple[str, str]:
    """进程内 WSGI 一侧。**不复用 run_bench.run_case**——那是真网络实现。"""
    import hashlib

    import lab.routes as R
    from lab.app import App
    from lab.transport_oob import LabTransport

    from veripen.bench import Outcome
    from veripen_oracles import ALL_ORACLES
    from veripen_oracles.common.harness import SymbolTable, run as run_oracle

    app = App(secure=False) if case.label == "positive" else App(secure=True)
    if "degenerate" in case.seeds:
        app = App(secure=False)
        app.degenerate = True

    try:
        binding = R.binding_for(case.selector)
    except LookupError:
        return "not_run", "BLOCKED"

    oracle = next((c() for c in ALL_ORACLES if c.meta.id == case.oracle), None)
    if oracle is None:
        return "error", "BLOCKED"
    transport = LabTransport(app)
    bindings = {case.selector: binding}
    for tag in extra_tags(case.oracle):
        try:
            bindings[tag] = R.binding_for(tag)
        except LookupError:
            return "not_run", "BLOCKED"
    try:
        result = run_oracle(
            oracle, binding, transport,
            symbols=SymbolTable(SYMBOLS),
            nonce=f"bench-{case.case_id}",
            token=hashlib.sha256(case.case_id.encode()).hexdigest(),
            bindings=bindings,
        )
    except Exception:
        return "error", "BLOCKED"
    verdict = result.verdict.value
    outcome = Outcome.UNDETERMINED.value if verdict == "INCONCLUSIVE" else Outcome.REPORTED.value
    return outcome, verdict


def _on_network(case) -> tuple[str, str]:
    """真网络一侧。"""
    from test_bench_on_network import _run_on_network

    return _run_on_network(case)


def main() -> int:
    cases = load_cases(ROOT / "tests" / "bench" / "cases.yaml")
    diffs: list[tuple[str, str, str, str, str]] = []
    stats = {"wsgi_reported": 0, "net_reported": 0, "wsgi_error": 0, "net_error": 0}

    for case in cases:
        w_outcome, w_verdict = _in_process(case)
        outcome, verdict = _on_network(case)

        if w_outcome == "reported":
            stats["wsgi_reported"] += 1
        if w_outcome == "error":
            stats["wsgi_error"] += 1
        if outcome == "reported":
            stats["net_reported"] += 1
        if outcome == "error":
            stats["net_error"] += 1

        if (w_outcome, w_verdict) != (outcome, verdict):
            diffs.append((case.case_id, w_outcome, w_verdict, outcome, verdict))

    print(json.dumps({
        "cases": len(cases),
        "diff_count": len(diffs),
        "stats": stats,
        "diffs": [
            {"case": c, "wsgi": f"{o}/{v}", "net": f"{no}/{nv}"}
            for c, o, v, no, nv in diffs
        ],
    }, ensure_ascii=False, indent=2))
    return 0 if not diffs else 1


if __name__ == "__main__":
    raise SystemExit(main())
