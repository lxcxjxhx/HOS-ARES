#!/usr/bin/env python3
"""veripen cases —— A7「接口能自动覆盖」的命令行形态（`19`）。

用法：
    python -m veripen_cli.cmd_cases --contract tests/fixtures/contract/sample.json
    python -m veripen_cli.cmd_cases --contract X --check          # 跑两次比 routeset_sha256
    python -m veripen_cli.cmd_cases --contract X --out out/cases

**这个命令只证明三件事**，多一件都不证明：

1. **契约能被解析**：OpenAPI → OperationSet（`parse_openapi`），标签来自契约/确认表。
2. **解析结果稳定**：同一份契约跑两次，`routeset_sha256` 必须逐字相同。
   不稳定就说明排序键里混进了 `dict` 迭代序或时间戳——那是"两次结果不同但都'对'"的开始。
3. **不产地址**：候选里的 `template` 是**相对模板**（`/api/users/{id}` 形态），
   绝不含 scheme/host/port。这一条现场验证，不靠 `lint-noroute` 事后发现。

**它不证明**「40 op → 200 绑定」这个数字（`21 §B8` 的原始 DoD）。
那个数字依赖具体客户契约，本仓库只有样例契约；把样例的候选数写死成断言
等于把"能覆盖多少"抄成常量。这里如实打印实际数量，并给出 `skipped` 的分因。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ._io import setup_stdout  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from veripen_contract.parser import ContractError, OperationSet, parse_openapi  # noqa: E402
from veripen_contract.planner import PlanResult, plan  # noqa: E402
from veripen_core.routes import (  # noqa: E402
    AssetRef,
    Resolver,
    RouteHints,
    RouteProvenance,
    RouteQuery,
)
from veripen_core.routes.sources.contract import ContractSource  # noqa: E402


def _build(text: str, confirm: tuple[str, ...]) -> tuple[OperationSet, PlanResult]:
    # asset_id 只做归属标注，不是地址；样例契约没有 servers 段，故不给 allowed_hosts，
    # 这样"契约自述了别人的 host"这种情况仍会被 parse_openapi 拒掉（VP-1006）。
    opset = parse_openapi(text, asset_id="cases-cli")
    result = plan(opset, source=RouteProvenance.CONTRACT, confirm=confirm)
    return opset, result


def _sha_of(result: PlanResult) -> str:
    """对候选集算一个稳定摘要：只含结构与相对模板，不含任何地址。"""
    import hashlib

    payload = [
        {
            "template": c.template,
            "method": c.method,
            "param_names": list(c.param_names),
            "tags": list((c.response_shape or {}).get("semantic_tags") or ()),
            "template_sha256": c.template_sha256,
        }
        for c in result.candidates
    ]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _has_address(template: str) -> bool:
    """候选模板里出现 scheme 或 host:port 就是漏了地址。"""
    low = template.lower()
    return "://" in low or low.startswith("//")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="veripen cases")
    ap.add_argument("--contract", required=True, help="OpenAPI JSON 路径")
    ap.add_argument("--out", default=str(ROOT / "out" / "cases"))
    ap.add_argument("--check", action="store_true", help="跑两次比摘要（A7 的核心断言）")
    ap.add_argument("--confirm", nargs="*", default=(), help="operator 确认的标签")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    path = Path(args.contract)
    if not path.is_file():
        setup_stdout()
        print(f"cases: FAIL: 契约不存在 {path}")
        return 1

    text = path.read_text(encoding="utf-8")
    try:
        opset, result = _build(text, tuple(args.confirm))
    except ContractError as exc:
        setup_stdout()
        print(f"cases: FAIL: 契约解析失败（{type(exc).__name__}）：{exc}")
        return 1

    sha1 = _sha_of(result)
    drifted = False
    if args.check:
        _, result2 = _build(text, tuple(args.confirm))
        sha2 = _sha_of(result2)
        drifted = sha1 != sha2

    leaked = [c.template for c in result.candidates if _has_address(c.template)]

    skipped_reasons: dict[str, int] = {}
    for _op_id, reason in result.skipped:
        skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "contract": str(path),
        "operations": len(opset.operations),
        "candidates": len(result.candidates),
        "skipped": len(result.skipped),
        "skipped_reasons": skipped_reasons,
        "candidates_sha256": sha1,
        "stable_twice": (not drifted) if args.check else None,
        "address_leaks": leaked,
        "templates": [c.template for c in result.candidates],
    }
    (out_dir / "cases.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if args.json:
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 0 if not drifted and not leaked else 1

    setup_stdout()
    print(f"contract : {path.name}")
    print(f"operations: {len(opset.operations)}  candidates: {len(result.candidates)}  "
          f"skipped: {len(result.skipped)}")
    if skipped_reasons:
        print("  skipped 分因：" + ", ".join(f"{k}={v}" for k, v in sorted(skipped_reasons.items())))
    print(f"摘要     : {sha1[:16]}")
    if args.check:
        print(f"稳定两次 : {'OK' if not drifted else 'FAIL（两次摘要不同 → 排序键不稳定）'}")
    if leaked:
        print(f"❌ 候选里出现地址：{leaked[:3]}")
    else:
        print("零地址   : OK（候选模板均不含 scheme/host）")
    print(f"证据     : {out_dir / 'cases.json'}")
    return 0 if not drifted and not leaked else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
