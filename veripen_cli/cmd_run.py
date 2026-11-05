"""B66 · `veripen run`：端到端渗透链路（`H13 B66`）。

一条命令走完全程：契约解析 → 路由解析 → oracle 选择 → 执行 →
capsule 封存 → 报告出具。

与 `cmd_hunt` 的分工：
- `hunt` 是"人盯着跑"，只打印；
- `run` 是"跑完给你交付物"——多出**证据封存**与**报告出具**两段，
  并且**没有可跑 oracle 时退出码非 0**（不静默成功）。

证据链：VERIFIED 的结论各产一份 capsule，按报告顺序 `chain_capsules` 串链后落盘。
`capsule_id` 不再是 `sha256:<oracle>` 这种凭空字符串——那是 B46 之前的占位。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from veripen_contract.parser import ContractError, parse_openapi  # noqa: E402
from veripen_contract.planner import plan  # noqa: E402
from veripen_core.evidence import (  # noqa: E402
    CapsuleDoc,
    chain_capsules,
    save_capsule,
    verify_self_sha,
)
from veripen_core.routes import RouteProvenance  # noqa: E402
from veripen_oracles import ALL_ORACLES  # noqa: E402
from veripen_oracles.common.harness import SymbolTable  # noqa: E402
from veripen_oracles.common.harness import run as run_oracle  # noqa: E402
from veripen_oracles.live import CoreTransport  # noqa: E402

from ._io import setup_stdout  # noqa: E402
from .cmd_hunt import _build_routeset, _host_of, _oracles_for, build_guard  # noqa: E402

#: 证据目录名（相对 --out）
CAPSULE_DIR = "capsules"

#: 默认排除：写类与压力类（与 hunt 同口径）
DEFAULT_EXCLUDE = "rate_limit_missing,mass_assignment,xss_stored_probe"


def _meta(oracle: Any, key: str, default: Any = "") -> Any:
    return getattr(getattr(oracle, "meta", None), key, default)


def _findings_view(
    *, asset_id: str, oracle_id: str, meta: Any, result: Any,
    binding: Any, capsule: CapsuleDoc | None,
) -> Any:
    """一次执行 → 渲染用 finding。**verdict 只来自 result，不在这里改。**"""
    from veripen.report.render import FindingView, Outcome

    verdict = str(getattr(result.verdict, "value", result.verdict))
    # 四态映射（09 §4）：REFUTED 是"测过但没有该问题"，**不进 REPORTED**——
    # 否则"没漏洞"会被算成"已验证"，那是报告层最忌讳的一类错。
    if verdict == "VERIFIED":
        outcome = Outcome.REPORTED
    elif verdict in ("INCONCLUSIVE", "BLOCKED"):
        outcome = Outcome.UNDETERMINED
    elif verdict == "REFUTED":
        outcome = Outcome.NOT_RUN
    else:
        outcome = Outcome.ERROR
    return FindingView(
        finding_id=f"F-{oracle_id}-{str(getattr(binding, 'ref', ''))[:8]}",
        title=str(_meta(meta, "name", oracle_id)),
        asset_id=asset_id,
        oracle_id=oracle_id,
        cwe=str(_meta(meta, "cwe", "")),
        severity=str(_meta(meta, "severity", "medium")),
        confidence=1.0 if verdict == "VERIFIED" else 0.0,
        route_strength=str(getattr(binding, "strength", "contract") or "contract"),
        route_ref=str(getattr(binding, "ref", "") or ""),
        capsule_id=(capsule.capsule_id if capsule else None),
        self_sha256=(capsule.chain.self_sha256 if capsule else None),
        replay_status="NEVER",
        situation=str(getattr(result, "reason", "") or "")[:200],
        outcome=outcome,
        reason_code=str(getattr(result, "reason", "") or "")[:60],
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="veripen run", description="端到端渗透：跑完给交付物")
    ap.add_argument("--target", required=True, help="目标根地址（scheme + host[:port]）")
    ap.add_argument("--openapi", required=True, help="该目标的 OpenAPI 文档路径")
    ap.add_argument("--out", default=str(ROOT / "out" / "run"))
    ap.add_argument("--exclude", default=DEFAULT_EXCLUDE, help="逗号分隔的 oracle 黑名单")
    ap.add_argument("--token", default="", help="主身份 token（可空；空则相关 oracle 落 BLOCKED）")
    ap.add_argument("--max-oracles", type=int, default=0, help="0 = 全跑")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    setup_stdout()

    host = _host_of(args.target)
    text = Path(args.openapi).read_text(encoding="utf-8")
    try:
        opset = parse_openapi(text, asset_id=host, allowed_hosts=[host])
    except ContractError as exc:
        print(f"契约拒绝：{exc}", file=sys.stderr)
        return 2

    planned = plan(opset, source=RouteProvenance.CONTRACT)
    routeset = _build_routeset(args.target, list(planned.candidates), host)
    excluded = {x.strip() for x in args.exclude.split(",") if x.strip()}
    oracles = [o for o in _oracles_for(routeset) if o not in excluded]
    if args.max_oracles > 0:
        oracles = oracles[: args.max_oracles]

    print(f"目标：{host}")
    print(f"路由：{len(routeset.bindings)} 条绑定 / 可跑 oracle {len(oracles)} 个")
    if not oracles:
        print("没有可跑的 oracle（契约无语义标签或全被排除）", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    capsules_dir = out / CAPSULE_DIR

    transport = CoreTransport(timeout_s=10.0)
    # 出网闸门：没有它任何请求都抛 GuardMissing（闸门不是可选项）
    from veripen_core.tools import http as http_tool

    guard = build_guard(host, routeset, tuple(oracles))
    http_tool.install_guard(guard)
    started = time.time()
    results: list[tuple[str, Any, Any, Any]] = []   # (oracle_id, meta, result, binding)
    try:
        for oracle_id in oracles:
            cls = next((c for c in ALL_ORACLES if c.meta.id == oracle_id), None)
            if cls is None:
                continue
            oracle = cls()
            tags = [str(t) for t in getattr(oracle.meta, "selector_tags", ())]
            bindings: dict[str, Any] = {}
            for tag in tags:
                for b in routeset.bindings:
                    if tag in b.semantic_tags:
                        bindings[tag] = b
                        break
            primary = bindings.get(tags[0]) if tags else None
            if primary is None:
                print(f"  {oracle_id:<24} 跳过：路由未解析")
                continue
            try:
                # 闸门按 oracle 放行：不设这一项，请求会被 plan_denied 拦下
                transport.oracle_id = oracle_id
                result = run_oracle(
                    oracle, primary, transport,
                    symbols=SymbolTable(), nonce=f"run-{oracle_id}",
                    token=args.token or None, bindings=bindings,
                )
            except Exception as exc:  # noqa: BLE001 —— 目标上什么都可能发生，如实记
                print(f"  {oracle_id:<24} 执行异常：{type(exc).__name__}: {str(exc)[:60]}")
                continue
            verdict = str(getattr(result.verdict, "value", result.verdict))
            print(f"  {oracle_id:<24} {verdict:<14} {str(getattr(result, 'reason', ''))[:50]}")
            results.append((oracle_id, oracle.meta, result, primary))


    finally:
        http_tool.uninstall_guard()
    if not results:
        print("全部 oracle 未产出结果", file=sys.stderr)
        return 1

    # ── 证据封存：只为 VERIFIED 造 capsule，按报告顺序串链 ──────────
    from veripen.bench.capsule import build_capsule

    docs: list[CapsuleDoc] = []
    index_of: dict[str, CapsuleDoc] = {}
    for oracle_id, meta, result, binding in results:
        if str(getattr(result.verdict, "value", result.verdict)) != "VERIFIED":
            continue
        try:
            doc = build_capsule(
                case_id=f"run-{oracle_id}",
                oracle_id=oracle_id,
                oracle_version=str(_meta(meta, "version", "1")),
                verdict=result.verdict,
                judgement=result.judgement,
                pairs=result.pairs,
                baseline_params=result.baseline_params,
                routeset=routeset,
                route_ref=str(getattr(binding, "ref", "")),
                binding=binding,
                cwe=str(_meta(meta, "cwe", "")),
                created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
        except Exception as exc:  # noqa: BLE001 —— 证据不全就明说，不造假的
            print(f"  证据构造失败 {oracle_id}：{type(exc).__name__}: {str(exc)[:60]}")
            continue
        docs.append(doc)
        index_of[oracle_id] = doc

    sealed = chain_capsules(docs) if docs else []
    for cap in sealed:
        save_capsule(cap, capsules_dir)
        index_of[cap.oracle.id] = cap

    # ── 报告 ────────────────────────────────────────────────────
    from veripen.report.render import ReportInput, render_json, render_markdown

    views = [
        _findings_view(
            asset_id=host, oracle_id=oid, meta=meta, result=res,
            binding=binding, capsule=index_of.get(oid),
        )
        for oid, meta, res, binding in results
    ]
    report = ReportInput(
        engagement=host,
        profile_hash="",
        image_digest="",
        root_sha256=(sealed[0].chain.self_sha256 if sealed else ""),
        routeset_sha256=[str(getattr(routeset, "routeset_sha256", ""))],
        resolver_version=str(getattr(routeset, "resolver_version", "")),
        findings=views,
        render_time=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )

    (out / "findings.json").write_text(
        json.dumps(render_json(report), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "report.md").write_text(render_markdown(report), encoding="utf-8")

    verified = sum(1 for v in views if v.outcome.value == "reported")
    print("-" * 62)
    print(f"结论：VERIFIED {verified} / 共 {len(views)} 项")
    print(f"证据：{len(sealed)} 份 capsule → {capsules_dir}")
    print(f"交付：{(out / 'report.md').name} / {(out / 'findings.json').name}")
    print(f"耗时：{time.time() - started:.1f}s")
    bad = [c for c in sealed if not verify_self_sha(c)]
    if bad:
        print(f"证据自校验失败 {len(bad)} 份", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
