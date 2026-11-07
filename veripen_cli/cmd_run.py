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
from veripen_contract.spec_detect import SpecFormatError, as_openapi_text  # noqa: E402
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

from ._discover_args import add_discovery_args
from ._io import setup_stdout  # noqa: E402
from .cmd_hunt import _build_routeset, _host_of, _oracles_for, build_guard  # noqa: E402

#: 证据目录名（相对 --out）
CAPSULE_DIR = "capsules"

#: 默认排除：写类与压力类（与 hunt 同口径）
DEFAULT_EXCLUDE = "rate_limit_missing,mass_assignment,xss_stored_probe"


def _meta(oracle: Any, key: str, default: Any = "") -> Any:
    """读 oracle 元信息。**同时接受 oracle 与 oracle.meta 两种传法。**

    早期实现写的是 ``getattr(getattr(oracle, "meta", None), key, default)``，
    而调用方传进来的已经是 ``oracle.meta``——再取一次 ``.meta`` 得到 ``None``，
    于是**每一次**都落到默认值：severity 恒 medium、cwe 恒空、标题恒为 oracle id。
    报告看起来"有内容"，但每一条的严重度都是编出来的。
    """
    meta = getattr(oracle, "meta", oracle)
    return getattr(meta, key, default)


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
        finding_id=f"F-{oracle_id}-{_ref_tail(binding)}",
        title=str(_meta(meta, "name", oracle_id)),
        asset_id=asset_id,
        oracle_id=oracle_id,
        cwe=str(_meta(meta, "cwe", "")),
        severity=_severity_of(meta, oracle_id, _strength_of(binding))[0],
        confidence=1.0 if verdict == "VERIFIED" else 0.0,
        route_strength=_strength_of(binding),
        route_ref=str(getattr(binding, "ref", "") or ""),
        capsule_id=(capsule.capsule_id if capsule else None),
        self_sha256=(capsule.chain.self_sha256 if capsule else None),
        replay_status="NEVER",
        situation=_situation(meta, oracle_id, binding, result),
        outcome=outcome,
        reason_code=str(getattr(result, "reason", "") or "")[:60],
    )


#: 基址符号名：地址由符号值给，模板里不写地址（N3）。
BASE_SYMBOL = "base"
SEP = chr(0x2F)


def _discover_routeset(args: Any, host: str) -> Any:
    """无契约路径：轮次发现 → 最终 RouteSet。

    抓链接要发请求，发请求必须持 ``RouteBinding``，而绑定只能由 resolver 产出。
    所以一轮只抓已绑定的面，新面交回下一轮——地址纪律不破例。

    最终把**所有**观测记录一次性喂给 resolver：面本身来自观测（crawl 0.70），
    标签由看见的形状推出来（``routes/tagging.py``），并在 ``tag_sources`` 里标 structural。
    """
    from veripen_core.discovery import (
        Crawler,
        candidate_of,
        seed_candidate,
        transport_fetcher,
        well_known_candidates,
    )
    from veripen_core.routes import AssetRef, Resolver, RouteHints, RouteQuery
    from veripen_core.routes.sources.base import ObservedRecord, candidate_from_record
    from veripen_core.routes.sources.symbols import (
        SymbolSource as SymSource,
        SymbolTable as RouteSymbols,
    )
    from veripen_core.tools import http as http_tool

    import contextlib

    symbols = RouteSymbols({BASE_SYMBOL: args.target.rstrip(SEP)})
    stack = contextlib.ExitStack()
    renderer = None
    if getattr(args, "render", False):
        from veripen_core.render.browser import BrowserRenderer

        renderer = stack.enter_context(BrowserRenderer())
    crawler = Crawler(transport_fetcher(), max_pages=args.discover_pages, renderer=renderer,
            submit_forms=getattr(args, "submit_forms", False),
            ignore_params=(BASE_SYMBOL,))
    candidates = [seed_candidate(BASE_SYMBOL), *well_known_candidates(BASE_SYMBOL)]
    records: list[ObservedRecord] = []
    seen: set[tuple[str, str]] = set()

    def _resolve(cands: list[Any]) -> Any:
        src = SymSource(
            "crawl", RouteProvenance.CRAWL, cands, symbols, base_symbol=BASE_SYMBOL
        )
        query = RouteQuery(
            asset=AssetRef(id=host), hints=RouteHints(), min_confidence=0.0
        )
        return Resolver(sources=[src]).resolve(query, now=int(time.time()))

    for round_no in range(1, max(1, args.discover_rounds) + 1):
        routeset = _resolve(candidates)
        if not routeset.bindings:
            print(f"发现第 {round_no} 轮：解析出 0 条绑定，停。", file=sys.stderr)
            break
        guard = build_guard(host, routeset, ("discover",))
        guard.oracle_id = "discover"
        http_tool.install_guard(guard)
        try:
            step = crawler.step(routeset.bindings)
        finally:
            http_tool.uninstall_guard()
        fresh = 0
        for rec in step.records:
            key = (rec.method, rec.template)
            if key in seen:
                continue
            seen.add(key)
            records.append(rec)
            fresh += 1
        print(f"发现第 {round_no} 轮：抓 {step.fetched} 页 / 新面 {fresh} 条")
        if not step.next:
            break
        candidates = [candidate_of(s, BASE_SYMBOL) for s in step.next]

    final = [
        candidate_from_record(rec, source=RouteProvenance.CRAWL, prefix="{" + BASE_SYMBOL + "}")
        for rec in records
    ]
    final.insert(0, seed_candidate(BASE_SYMBOL))
    stack.close()
    return _resolve(final)


#: 弱证据面（09 §3.1）：这些面出来的结论不得标高严重度。
WEAK_STRENGTHS = frozenset({"crawl", "probe"})
HIGH_SEVERITIES = frozenset({"high", "critical"})


def _ref_tail(binding: Any, length: int = 8) -> str:
    """面标识的尾巴：``route://contract/abcd1234…`` → ``abcd1234``。

    早期直接 ``ref[:8]`` —— 而 ``route://`` 恰好 8 个字符，于是**同一个 oracle 的
    所有 finding 拿到同一个 id**（都与面无关），详情页按 id 选人时永远选中第一条。
    """
    ref = str(getattr(binding, "ref", "") or "")
    tail = ref.rsplit(chr(0x2F), 1)[-1]
    return tail[:length] or "noref"


def _strength_of(binding: Any) -> str:
    """面的证据强度：**由置信档推出来，不写死 contract**。

    早期版本 ``getattr(binding, "strength", "contract")`` —— 而 ``RouteBinding``
    根本没有 ``strength`` 字段，于是 discovery 抓来的面（crawl 0.70）在报告里
    被写成 contract（0.90）。那是把"猜的面"和"契约声明的面"混成同一档，
    正是 09 §3.1 要防的事。
    """
    from veripen_core.routes.confidence import strength_of

    return strength_of(binding)


def _severity_of(meta: Any, oracle_id: str, strength: str) -> tuple[str, str]:
    """→ (severity, basis)。等级来自 **policy 的规则表**，不是模型也不是默认值。

    两道约束叠在一起：
    1. ``severity_for`` 把 ``severity_rule`` 映射成等级（表外的规则兜底 medium 并标 default）；
    2. 弱面（crawl/probe）不得标高严重度——压到 medium 并改 basis 为 ``weak_face_cap``。

    ``basis`` 会写进 ``situation``：报告读者要能分清"评估给出"和"兜底/降级给出"。
    """
    from veripen_oracles.policy import severity_for

    rule = str(_meta(meta, "severity_rule", ""))
    severity, basis = severity_for(oracle_id, rule)
    if strength in WEAK_STRENGTHS and severity in HIGH_SEVERITIES:
        return "medium", "weak_face_cap"
    return severity, basis


def _situation(meta: Any, oracle_id: str, binding: Any, result: Any) -> str:
    """现况一句话。**严重度的来路写在这里**，而不是靠读者猜 medium 是评估还是兜底。"""
    strength = _strength_of(binding)
    _severity, basis = _severity_of(meta, oracle_id, strength)
    prefix = ""
    if basis == "weak_face_cap":
        prefix = f"弱面（{strength}）：严重度按 medium 计。"
    elif basis == "default":
        prefix = "oracle 未声明该规则对应的严重度，按 medium 兜底。"
    return (prefix + str(getattr(result, "reason", "") or ""))[:200]


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="veripen run", description="端到端渗透：跑完给交付物")
    ap.add_argument("--target", required=True, help="目标根地址（scheme + host[:port]）")
    ap.add_argument("--openapi", default="", help="OpenAPI/Swagger 文档路径（与 --discover 二选一）")
    add_discovery_args(ap, include_enable=True, legacy_aliases=False)
    ap.add_argument("--out", default=str(ROOT / "out" / "run"))
    ap.add_argument("--exclude", default=DEFAULT_EXCLUDE, help="逗号分隔的 oracle 黑名单")
    ap.add_argument("--token", default="", help="主身份 token（可空；空则相关 oracle 落 BLOCKED）")
    ap.add_argument("--max-oracles", type=int, default=0, help="0 = 全跑")
    ap.add_argument(
        "--confirm-structural",
        action="store_true",
        help="采纳 parser 的结构推断标签（source=structural，置信 0.80）；"
             "不带时与今天一致：只认契约显式标签",
    )
    ap.add_argument(
        "--confirm-tags",
        default="",
        help="逗号分隔的标签名：只采纳这些（来自 profile 的 selectors，即 operator 确认）",
    )
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    setup_stdout()

    host = _host_of(args.target)
    if bool(args.openapi) == bool(args.discover):
        print("--openapi 与 --discover 必须二选一", file=sys.stderr)
        return 2

    planned = None
    if args.discover:
        # 无契约：面的来源是观测（crawl），标签由看见的形状推出来（B94）。
        routeset = _discover_routeset(args, host)
        inferred = {
            tag
            for b in routeset.bindings
            for tag in b.semantic_tags
        }
    else:
        # 契约格式先归一（B89）：swagger 2.0 会在这里折成 OpenAPI 3；
        # Postman/认不出的格式当场说清楚，不要等到 parser 报"paths 不是对象"。
        try:
            text = as_openapi_text(Path(args.openapi).read_text(encoding="utf-8"))
        except SpecFormatError as exc:
            print(f"契约格式不支持：{exc}", file=sys.stderr)
            return 2
        try:
            opset = parse_openapi(text, asset_id=host, allowed_hosts=[host])
        except ContractError as exc:
            print(f"契约拒绝：{exc}", file=sys.stderr)
            return 2
        planned = plan(
            opset, source=RouteProvenance.CONTRACT,
            confirm={t.strip() for t in args.confirm_tags.split(",") if t.strip()},
            accept_structural=args.confirm_structural,
        )
        routeset = _build_routeset(args.target, list(planned.candidates), host)
        inferred = {
            tag
            for c in planned.candidates
            for tag, src in (c.response_shape.get("tag_sources") or {}).items()
            if src == "structural"
        }
    excluded = {x.strip() for x in args.exclude.split(",") if x.strip()}
    oracles = [o for o in _oracles_for(routeset) if o not in excluded]
    if args.max_oracles > 0:
        oracles = oracles[: args.max_oracles]

    print(f"目标：{host}")
    print(f"路由：{len(routeset.bindings)} 条绑定 / 可跑 oracle {len(oracles)} 个")
    if inferred:
        where = "观测推断" if args.discover else "结构推断"
        print(f"标签：{where} {len(inferred)} 类（{', '.join(sorted(inferred))}）"
              f"—— 置信 0.80，低于契约显式 0.95")
    if not oracles:
        # **照样出账本**：一个面都没判，正是最需要写清"为什么"的时候。
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        from veripen_core.coverage import build_coverage, write_coverage

        selectors = {
            str(c.meta.id): tuple(str(t) for t in getattr(c.meta, "selector_tags", ()))
            for c in ALL_ORACLES
        }
        empty = build_coverage(
            asset_id=host,
            bindings=routeset.bindings,
            oracle_tags=selectors,
            selected=(),
            observations=(),
            excluded=sorted(excluded),
            run_status="no_oracle",
            exit_reason="no_selectable_oracle",
        )
        write_coverage(out, empty)
        print("没有可跑的 oracle —— 逐面原因：", file=sys.stderr)
        for binding in routeset.bindings:
            tags_of = tuple(getattr(binding, "semantic_tags", ()) or ())
            if not tags_of:
                why = "这条面没有语义标签（响应形状推不出类型、也没有参数）"
            else:
                hit = sorted({oid for oid, sel in selectors.items() if sel and sel[0] in tags_of})
                why = (
                    "标签 " + ", ".join(tags_of) + " 没有对应 oracle"
                    if not hit
                    else "标签 " + ", ".join(tags_of) + " 的 oracle 全被 --exclude 排除"
                )
            print(f"  - {str(getattr(binding, 'template', ''))[:70]}：{why}", file=sys.stderr)
        print(f"账本：{out / 'coverage.json'}（缺口 {len(empty['gaps'])} 条）", file=sys.stderr)
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
    # ── 覆盖账本（B87）：先算账，再写报告——报告里要带缺口清单 ──────
    from veripen_core.coverage import Observation, build_coverage, write_coverage

    oracle_tags = {
        str(c.meta.id): tuple(str(t) for t in getattr(c.meta, "selector_tags", ()))
        for c in ALL_ORACLES
    }
    observations = [
        Observation(
            oracle_id=oid,
            route_ref=str(getattr(binding, "ref", "")),
            verdict=str(getattr(res.verdict, "value", res.verdict)),
            reason=str(getattr(res, "reason", "") or "")[:200],
        )
        for oid, _meta, res, binding in results
    ]
    coverage = build_coverage(
        asset_id=host,
        bindings=routeset.bindings,
        oracle_tags=oracle_tags,
        selected=oracles,
        observations=observations,
        excluded=sorted(excluded),
    )

    report = ReportInput(
        engagement=host,
        profile_hash="",
        image_digest="",
        root_sha256=(sealed[0].chain.self_sha256 if sealed else ""),
        routeset_sha256=[str(getattr(routeset, "routeset_sha256", ""))],
        resolver_version=str(getattr(routeset, "resolver_version", "")),
        findings=views,
        # 报告第 4 节要能把"哪条面没被判定"点名，而不只给一个统计
        coverage={"gaps": coverage["gaps"], "summary": coverage["summary"],
                  "completeness": coverage["completeness"]},
        # 面是怎么来的：无契约时必须在报告头写清，否则读者会当成契约面
        discovery=(
            {"mode": "discover", "rounds": args.discover_rounds,
             "pages": args.discover_pages}
            if args.discover else {}
        ),
        render_time=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )

    (out / "findings.json").write_text(
        json.dumps(render_json(report), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "report.md").write_text(render_markdown(report), encoding="utf-8")

    verified = sum(1 for v in views if v.outcome.value == "reported")
    print("-" * 62)
    print(f"结论：VERIFIED {verified} / 共 {len(views)} 项")

    cov_path = write_coverage(out, coverage)
    cov_summary = coverage["summary"]
    print(
        f"覆盖：{cov_summary['faces_covered']}/{cov_summary['faces']} 面"
        f" / 适用 {cov_summary['applicable_pairs']} 对 / 执行 {cov_summary['executed']}"
        f" / 缺口 {cov_summary['gaps']} → {cov_path.name}"
    )
    if not coverage["completeness"]["complete"]:
        for caveat in coverage["completeness"]["caveats"]:
            print(f"  注意：{caveat}")

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
