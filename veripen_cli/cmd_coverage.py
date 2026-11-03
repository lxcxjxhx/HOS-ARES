#!/usr/bin/env python3
"""veripen coverage-matrix —— A6「覆盖够宽」的命令行形态（`18 §0` / `16 §4`）。

用法：python -m veripen_cli.cmd_coverage [--cases tests/bench/cases.yaml] [--out out/coverage]

回答一个问题：**14 类 oracle × 已登记标签，哪些真的有用例、哪些是空的。**

三条口径，缺一条这个矩阵就变成装饰：

1. **分母是真的 14 类**（`18 §0`），不是「已注册多少算多少」——
   注册表多一个别名（`jwt_suite`）不该让分母变大，少一类也不该让它变小。
2. **命中按 oracle id 精确匹配**，不做模糊/前缀匹配：模糊匹配会把
   「某类没覆盖」洗成「覆盖了」。
3. **0 用例的 oracle 必须显式列出**并计入未覆盖数——`13 R14` 说的
   「诚实报未覆盖」在这里是机械的，不是自觉的。

退出码：有 0 用例的 oracle → 1。这不是"失败"，是**让缺口可见**；
真要让 CI 过，必须补用例或显式在 `18 §0` 里标 `enabled:false`。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from ._io import setup_stdout  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from veripen.bench.core import load_cases  # noqa: E402
from veripen_oracles import ALL_ORACLES  # noqa: E402

#: `18 §0` 的 14 类判定矩阵。**这里是唯一权威**：注册表可以多（别名），
#: 但分母必须是规格说的那 14 个 id。少一个 = 规格与代码脱节，直接报错。
SPEC_14: tuple[str, ...] = (
    "sqli_bool_diff",
    "idor_cross_account",
    "ssrf_oob",
    "jwt_replay",
    "open_redirect",
    "path_traversal",
    "sqli_error_based",
    "bola_functional",
    "xss_reflected_ctx",
    "mass_assignment",
    "rate_limit_absent",
    "xss_stored_probe",
    "cors_misconfig",
    "graphql_introspection",
)

#: 明确不参与本期判定的别名（`jwt_suite` 是 `jwt_replay` 的认证扩展面）。
ALIASES = {"jwt_suite": "jwt_replay"}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="veripen coverage-matrix")
    ap.add_argument("--cases", default=str(ROOT / "tests" / "bench" / "cases.yaml"))
    ap.add_argument("--out", default=str(ROOT / "out" / "coverage"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    cases = load_cases(args.cases)
    registered = {c.meta.id: c.meta for c in ALL_ORACLES}

    # 规格 14 类必须全部注册——这是 spec_drift 的另一面，这里顺带查，
    # 因为"矩阵里有一行但代码没有"会让读者以为能测。
    missing_impl = [oid for oid in SPEC_14 if oid not in registered]

    by_oracle: Counter[str] = Counter()
    by_tag: Counter[str] = Counter()
    for case in cases:
        oid = case.oracle
        by_oracle[ALIASES.get(oid, oid)] += 1
        meta = registered.get(oid)
        if meta:
            for tag in meta.selector_tags:
                by_tag[tag] += 1

    rows: list[dict[str, object]] = []
    for oid in SPEC_14:
        meta = registered.get(oid)
        n = by_oracle.get(oid, 0)
        rows.append(
            {
                "oracle": oid,
                "cases": n,
                "selector_tags": list(meta.selector_tags) if meta else [],
                "tags_str": ",".join(meta.selector_tags) if meta else "",
                "cwe": meta.cwe if meta else "",
                "impact": meta.impact.value if meta else "",
                "covered": n > 0,
            }
        )

    uncovered = [str(r["oracle"]) for r in rows if not r["covered"]]
    # 反向：用例里出现了不在 14 类里的 oracle（拼错或新增未登记）
    unknown_in_cases = sorted(set(by_oracle) - set(SPEC_14))

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "denominator": len(SPEC_14),
        "cases_total": len(cases),
        "covered": len(SPEC_14) - len(uncovered),
        "uncovered": uncovered,
        "missing_impl": missing_impl,
        "unknown_in_cases": unknown_in_cases,
        "rows": rows,
        "by_tag": dict(sorted(by_tag.items())),
    }
    (out_dir / "coverage.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if not uncovered and not missing_impl else 1

    setup_stdout()
    print(f"{'oracle':22s} {'用例':>4s} {'覆盖':>4s}  {'impact':12s} {'cwe':10s} tags")
    print("-" * 96)
    for r in rows:
        mark = "OK" if r["covered"] else "--"
        print(
            f"{r['oracle']:22s} {r['cases']:4d} {mark:>4s}  "
            f"{r['impact']:12s} {r['cwe']:10s} {r['tags_str']}"
        )
    print("-" * 96)
    print(f"覆盖：{report['covered']}/{report['denominator']}  用例总数：{len(cases)}")
    if uncovered:
        print("⚠ 未覆盖（0 用例）：" + ", ".join(uncovered))
        print("  这不是失败，是缺口可见。补用例，或在 18 §0 显式标 enabled:false。")
    if missing_impl:
        print("❌ 规格有但代码无实现：" + ", ".join(missing_impl))
    if unknown_in_cases:
        print("⚠ 用例里的 oracle 不在 14 类内：" + ", ".join(unknown_in_cases))
    print(f"证据：{out_dir / 'coverage.json'}")
    return 0 if not uncovered and not missing_impl else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
