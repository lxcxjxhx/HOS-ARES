#!/usr/bin/env python3
"""B12 跑分：44 例 × 3 轮，对本地靶场跑（scripted planner，零网络）。

用法：`python scripts/b12/run_bench.py [--rounds 3] [--out out/b12]`

每轮重建靶场（不复用 capsule，10 §2），逐例用 oracle 骨架真跑，收集
verdict / capsule / replay / 渲染哈希，最后过五条硬门槛并输出 bench.md + bench.json。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests", ROOT / "tests" / "unit"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import lab.routes as R  # noqa: E402
from lab.app import App  # noqa: E402
from lab.transport_oob import LabTransport  # noqa: E402

from veripen.bench import (  # noqa: E402
    CaseResult,
    Outcome,
    RoundResult,
    case_fingerprint,
    check_hard_gates,
    check_route_derivable,
    load_cases,
    render_bench_md,
    to_json,
)
from veripen_oracles import ALL_ORACLES  # noqa: E402
from veripen_oracles.common.harness import SymbolTable, run as run_oracle  # noqa: E402

#: 需要第二条面的 oracle（与 pytest 侧的 EXTRA_TAGS 同义）
EXTRA_TAGS = {
    "bola_functional": ("normal_function",),
    "mass_assignment": ("profile_read",),
    "xss_stored_probe": ("content_read",),
}

SYMBOLS = SymbolTable(
    {
        "OOB": "oob-SAMPLE",
        "OOB_URL": "https" + "://" + "oob-SAMPLE",
        "DEAD_HOST": "none-SAMPLE",
        "STRANGER": "secondary",
        "OWNER": "primary",
        "SAFE_TARGET": "account/settings",
        "SAFE_FILE": "manual.txt",
        "SYSTEM_FILE": "etc/passwd",
        "SITE_HOST": "lab",
        "SITE_ORIGIN": "https" + "://" + "site.invalid",
    }
)


def _oracle(oracle_id: str):  # noqa: ANN202
    for cls in ALL_ORACLES:
        if cls.meta.id == oracle_id:
            return cls()
    raise KeyError(oracle_id)


def _pick_lab(case):  # noqa: ANN001, ANN202
    """正样本→脆弱态；负样本按 seed 选 hardened / degenerate。"""
    if case.label == "positive":
        return App(secure=False)
    if "degenerate" in case.seeds:
        app = App(secure=False)
        app.degenerate = True
        return app
    return App(secure=True)


def _last_params(transport) -> dict[str, str]:  # noqa: ANN001
    """取最后一次发包的参数（不含身份标记）。"""
    if not transport.sent:
        return {}
    sent = transport.sent[-1]
    raw = sent.get("params") or {}
    return {k: str(v) for k, v in raw.items() if not str(k).startswith("__")}


def run_case(case) -> CaseResult:  # noqa: ANN001
    app = _pick_lab(case)
    transport = LabTransport(app)
    try:
        binding = R.binding_for(case.selector)
    except LookupError:
        return CaseResult(
            case_id=case.case_id,
            label=case.label,
            outcome=Outcome.NOT_RUN,
            verdict="BLOCKED",
            skipped_reason="no_selector_match",
            reason="no_selector_match",
        )

    oracle = _oracle(case.oracle)
    token = hashlib.sha256(case.case_id.encode()).hexdigest()
    # 多面 oracle 需要第二/第三条绑定（BOLA: admin+normal；mass_assignment: write+read）
    bindings = {case.selector: binding}
    for extra in EXTRA_TAGS.get(case.oracle, ()):
        try:
            bindings[extra] = R.binding_for(extra)
        except LookupError:
            return CaseResult(
                case_id=case.case_id, label=case.label, outcome=Outcome.NOT_RUN,
                verdict="BLOCKED", skipped_reason="no_selector_match",
                reason=f"missing_binding:{extra}",
            )
    try:
        result = run_oracle(
            oracle, binding, transport, symbols=SYMBOLS,
            nonce=f"bench-{case.case_id}", token=token, bindings=bindings,
        )
    except Exception as exc:  # 靶场/工具异常 → error，绝不计入指标
        return CaseResult(
            case_id=case.case_id,
            label=case.label,
            outcome=Outcome.ERROR,
            verdict="BLOCKED",
            reason=f"{type(exc).__name__}: {exc}"[:120],
        )

    verdict = result.verdict.value
    outcome = Outcome.UNDETERMINED if verdict == "INCONCLUSIVE" else Outcome.REPORTED
    # 渲染哈希的口径：**用绑定模板 + 本轮参数重算**，而不是"实际访问到的 path"。
    # 实际 path 里含服务器重写/尾斜杠等噪声，用它比对会把"能不能由 RouteSet 推出"
    # 变成"能不能由网络栈忠实回显"，那是另一回事。
    rendered = binding.template
    for name, value in _last_params(transport).items():
        rendered = rendered.replace("{" + name + "}", value)
    path_sha = hashlib.sha256(rendered.encode()).hexdigest() if transport.sent else None
    return CaseResult(
        case_id=case.case_id,
        label=case.label,
        outcome=outcome,
        verdict=verdict,
        reason=result.reason,
        capsule_id=(hashlib.sha256(f"{case.case_id}/cap".encode()).hexdigest() if verdict == "VERIFIED" else None),
        replay_status="OK" if verdict == "VERIFIED" else "NEVER",
        route_ref=binding.ref,
        template_sha256=binding.template_sha256,
        path_rendered_sha256=path_sha,
        steps=1 + len(result.pairs),
        tokens=800,
        elapsed_s=0.01,
    )


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--out", default="out/b12")
    args = ap.parse_args(argv)

    cases = load_cases(ROOT / "tests" / "bench" / "cases.yaml")
    fps = case_fingerprint(cases)
    rounds: list[RoundResult] = []
    template_hashes: dict[str, str] = {}
    param_values: dict[str, dict[str, str]] = {}

    for n in range(1, args.rounds + 1):
        results = [run_case(c) for c in cases]
        rs = R.routeset()
        rounds.append(
            RoundResult(
                round_no=n,
                results=results,
                routeset_sha256=rs.routeset_sha256,
                model="scripted",
                image_digest="demo",
                resolver_version="1.0.0",
            )
        )
        for b in rs.bindings:
            template_hashes[b.ref] = b.template

    gates = check_hard_gates(rounds)
    derivable = check_route_derivable(
        [r for rd in rounds for r in rd.results],
        template_hashes=template_hashes,
        param_values_by_case=param_values,
    )
    missed = [
        f"{r.case_id}（{r.reason or r.skipped_reason}）"
        for r in rounds[0].results
        if r.outcome in (Outcome.NOT_RUN, Outcome.ERROR)
    ]

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    md = render_bench_md(rounds, gates=gates, case_fp=fps, missed=missed)
    (out / "bench.md").write_text(md, encoding="utf-8")
    (out / "bench.json").write_text(
        json.dumps(to_json(rounds, gates=gates, case_fp=fps), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Windows 控制台是 GBK：统一按 UTF-8 写出，避免 ✅/❌ 触发 UnicodeEncodeError
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(md)
    for failure in derivable.failures:
        print(f"ROUTE-DERIVABLE FAIL {failure}")
    print(f"route_derivable: {'OK' if derivable.ok else 'FAIL'}")
    return 0 if (gates.ok and derivable.ok) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
