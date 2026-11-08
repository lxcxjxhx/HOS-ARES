#!/usr/bin/env python3
"""B12 跑分：44 例 × 3 轮，**走真网络**（此前是进程内 WSGI，零 socket）。

用法：`python scripts/b12/run_bench.py [--rounds 3] [--out out/b12]`

每轮重建靶场（不复用 capsule，10 §2），逐例起一个 ``LabServer``(环回随机端口)，
用 ``NetTransport`` 真发包，逐例用 oracle 骨架真跑，收集 verdict / capsule / replay /
渲染哈希，最后过五条硬门槛并输出 bench.md + bench.json。

**证据真的落盘**（B150）：每轮的 capsule 串成一条链写进 ``<out>/capsules/<run>/r<N>/``
（``<run>`` 是本次运行的 UTC 时刻，重跑不会和上一轮混轮），bench.md 里写明路径。
此前只在内存里 seal、算个 ``capsule_id`` 就完事——报告说"证据 123 份"，盘上一份也没有，
读者无从重放（B149 的"指针指向不存在的文件"，换到 bench 又犯一次）。

**为什么每例一个新实例而不是共享服务**：``App`` 有 callbacks / log / state 三处可变字段，
共享方案必须逐字段重置，漏一个就让上一例副作用渗到下一例——那是最难查的一类假绿。
代价已量过（``tests/lab/bench_cost.py``）：单次起停 ~64ms，44 例 ~2.8s。可测的代价 vs 不可测的渗漏。

**基址从哪来**：租户的绑定模板不再来自 ``lab.routes``（那里是相对路径），而是经
``SymbolSource(base_symbol="LAB_BASE")`` 由 resolver 在解析期用符号值替换。
地址仍然只由 resolver 产出（27 N2），端口不进入任何模板或配置。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests", ROOT / "tests" / "unit"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import lab.routes as R  # noqa: E402
from lab.app import App, make_token  # noqa: E402
from lab.net_server import LabServer  # noqa: E402
from lab.net_transport import NetTransport  # noqa: E402

from veripen.bench import (  # noqa: E402
    CaseResult,
    Outcome,
    RoundResult,
    case_fingerprint,
    check_hard_gates,
    check_route_derivable,
    findings_fingerprint,
    load_cases,
    render_bench_md,
    to_json,
)
from veripen_core.routes import (  # noqa: E402
    AssetRef,
    Resolver,
    RouteHints,
    RouteProvenance,
    RouteQuery,
)
from veripen_core.routes.sources.symbols import SymbolSource as SymSource  # noqa: E402
from veripen_core.routes.sources.symbols import SymbolTable as RouteSymbols  # noqa: E402
from veripen.bench.capsule import (  # noqa: E402
    StepParamsTransport,
    build_capsule,
    save_round_chain,
    step_params,
)
from veripen.bench.faces import extra_tags  # noqa: E402
from veripen_core.evidence import (  # noqa: E402
    CapsuleDoc,
    ReplayEnv,
    ReplayMode,
    replay,
    seal_capsule_doc,
)
from veripen_oracles import ALL_ORACLES  # noqa: E402
from veripen_oracles.common.harness import SymbolTable, run as run_oracle  # noqa: E402
from veripen_oracles.oob import LabOob  # noqa: E402

#: 需要第二条面的 oracle 由 ``veripen.bench.faces`` 从 ``selector_tags`` 派生。
#: 这里不再留本地副本——它曾经有四份，其中 demo 那份已经分叉。

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

#: 基址符号名。模板里写 ``{LAB_BASE}``，解析期由 source 换成真 URL。
LAB_BASE = "LAB_BASE"


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


def _routeset(base: str, tags: tuple[str, ...]):  # noqa: ANN001, ANN202
    """按标签产出一个 RouteSet。**基址是符号值，模板仍由 resolver 给。**"""
    src = SymSource(
        "contract", RouteProvenance.CONTRACT, R.candidates(tags),
        RouteSymbols({LAB_BASE: base}), base_symbol=LAB_BASE,
    )
    q = RouteQuery(asset=AssetRef(id=R.ASSET), hints=RouteHints(), min_confidence=0.0)
    return Resolver(sources=[src]).resolve(q, now=R.NOW)


def _last_params(transport) -> dict[str, str]:  # noqa: ANN001
    """取最后一次发包的参数（不含身份标记）。"""
    if not transport.sent:
        return {}
    sent = transport.sent[-1]
    raw = sent.get("params") or {}
    return {k: str(v) for k, v in raw.items() if not str(k).startswith("__")}


#: case_id → 本轮实际发出的参数。route-derivable 闸门要拿它重算渲染哈希。
#: 用模块级字典而不是给 ``CaseResult`` 加字段：那是内核类型，本批不碰。
LAST_PARAMS: dict[str, dict[str, str]] = {}

#: TLS 材料（``--scheme https`` 时由 main() 填）。None → 走明文。
TLS: tuple[Path, Path] | None = None

#: 写进证据 env 块的客户档标识。scripted 档没有真模型，但"哪套配置跑的"必须留痕，
#: 否则同一批 capsule 分不清出自哪个档。
PROFILE_HASH = "scripted"

#: 本轮开始时刻（ISO-8601 UTC）。每轮开头由 main() 刷新，run_case 读它写进 capsule。
#: 用**真时钟**：证据上的时间必须是它产生的时间。三轮各不同没关系——
#: 没有任何地方比较跨轮的 capsule_id（findings_fingerprint 不含它）。
ROUND_CLOCK: list[str] = ["1970-01-01T00:00:00Z"]

#: 本轮造出的 capsule（**封印前**）。整轮跑完才串链，所以先攒着，由 main() 落盘。
#: 必须每轮清空：三个链头混在一个目录里，"整轮一条链"就没了。
ROUND_DOCS: list[CapsuleDoc] = []


def _replay_state(
    *,
    cap,  # noqa: ANN001
    app_factory,  # noqa: ANN001
    baseline_params,  # noqa: ANN001
    pairs,  # noqa: ANN001
) -> str:
    """在**干净实例**上重放一份刚 seal 的 capsule，返回真实的 ReplayStatus。

    为什么必须换一个新实例：重放要再发一轮请求，而写类 oracle
    （mass_assignment 写 profile、xss_stored_probe 写 note）在**同一实例**上
    重放会改掉被测状态，于是第二次响应必然与第一次不同——
    那样测出来的是"重放把靶场弄脏了"，不是"结论复不复现"。

    实测依据：上一版在同一实例上重放，8 条 VERIFIED 全落 PARTIAL，
    且正好是全部写类 / 有副作用的 oracle。换成新实例后应当复现。

    这同时是"跨环境迁移"的自然形态：新实例端口不同、身份相同
    （ref 由结构键派生），capsule 的 route 块照样解析得到。
    """
    try:
        app = app_factory()
        srv = LabServer(app, tls=TLS).start()
        try:
            # 传空标签 = 解析全部面：重放只需要"这个 ref 在这个环境里解析得到"，
            # 按 ref 反猜标签会引入第二次映射，错起来更难查。
            rs = _routeset(srv.base_url, ())
            if not rs.bindings:
                return "ROUTE_UNRESOLVED"
            transport = NetTransport(
                token_for=make_token, oob=LabOob(app=app, endpoint_value="oob-SAMPLE")
            )
            rt = StepParamsTransport(transport, step_params(cap, baseline_params, pairs))
            res = replay(cap, ReplayEnv(now=0, mode=ReplayMode.LIVE, routeset=rs), rt)
            return res.state.value
        finally:
            srv.stop()
    except Exception:
        return "FAILED"



def run_case(case) -> CaseResult:  # noqa: ANN001
    app = _pick_lab(case)
    t0 = time.perf_counter()
    srv = LabServer(app, tls=TLS).start()
    try:
        transport = NetTransport(
            token_for=make_token, oob=LabOob(app=app, endpoint_value="oob-SAMPLE")
        )
        try:
            primary_rs = _routeset(srv.base_url, (case.selector,))
            binding = primary_rs.bindings[0]
        except (LookupError, IndexError):
            return CaseResult(
                case_id=case.case_id,
                label=case.label,
                outcome=Outcome.NOT_RUN,
                verdict="BLOCKED",
                skipped_reason="no_selector_match",
                reason="no_selector_match",
            )

        oracle = _oracle(case.oracle)
        # 基线 token 必须是**靶场签发的合法 JWT**，不能是任意字符串。
        # jwt_suite 的整个前提是"拿一个合法会话去变造"（alg=none / 篡改声明 / 坏签名），
        # 传 sha256(case_id) 这种十六进制串，基线直接 401 → baseline_abnormal → BLOCKED，
        # 于是三条用例根本没被测（此前 P-07/P-08/N-06 就是这样白丢的）。
        token = make_token("primary")
        # 多面 oracle 需要第二/第三条绑定（BOLA: admin+normal；mass_assignment: write+read）
        bindings = {case.selector: binding}
        for extra in extra_tags(case.oracle):
            extra_rs = _routeset(srv.base_url, (extra,))
            if not extra_rs.bindings:
                return CaseResult(
                    case_id=case.case_id, label=case.label, outcome=Outcome.NOT_RUN,
                    verdict="BLOCKED", skipped_reason="no_selector_match",
                    reason=f"missing_binding:{extra}",
                )
            bindings[extra] = extra_rs.bindings[0]
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

        # ── 证据：把这次**真实执行**落成 capsule，并当场重放一遍 ────────
        # 必须在 srv 还活着时做：重放要真的再发一轮请求。
        # 此前这里是 sha256(case_id+"/cap") 与 "OK" if VERIFIED——两列都是常量，
        # 也就是说"每条发现能复现"从未被检验过。
        capsule_id: str | None = None
        replay_status = "NEVER"
        if result.verdict.value in ("VERIFIED", "REFUTED") and result.judgement is not None:
            cap = seal_capsule_doc(
                build_capsule(
                    case_id=case.case_id,
                    oracle_id=case.oracle,
                    oracle_version=str(getattr(oracle.meta, "version", "")),
                    verdict=result.verdict,
                    judgement=result.judgement,
                    pairs=result.pairs,
                    baseline_params=result.baseline_params,
                    routeset=primary_rs,
                    route_ref=binding.ref,
                    binding=binding,
                    bindings_by_tag=bindings,
                    profile_name="bench",
                    profile_hash=PROFILE_HASH,
                    image_digest="demo",
                    created_at=ROUND_CLOCK[0],
                    cwe=str(getattr(oracle.meta, "cwe", "") or ""),
                )
            )
            capsule_id = cap.capsule_id
            # 整轮结束后才串链落盘（见 main）：先攒着，报告指针最后由封印后的 id 回填。
            ROUND_DOCS.append(cap)
            # VERIFIED 与 REFUTED 都要重放：「找到洞」和「查过没洞」都是结论，
            # 后者占交付多数，不能因为它是"没有"就不给证据。
            if result.verdict.value in ("VERIFIED", "REFUTED"):
                replay_status = _replay_state(
                    cap=cap,
                    app_factory=lambda: _pick_lab(case),
                    baseline_params=result.baseline_params,
                    pairs=result.pairs,
                )
    finally:
        srv.stop()
    elapsed = time.perf_counter() - t0

    verdict = result.verdict.value
    # 10 §3.0：verdict=BLOCKED 的用例**绝对不计** TP/FP/FN/TN。
    # 原文："如果直接计入 TN 或『未发现』，precision/recall 会凭空变好看"——
    # 反向同样成立：计入 FN 会让 recall 凭空变难看，而那一例根本没被测。
    # 归 error（"没跑成"）而不是 not_run（"没跑"）：基线异常说明请求发出去了、
    # 但没有建立可比基线，属执行失败，该计入 error 率受 2% 硬门槛约束。
    if verdict == "BLOCKED":
        outcome = Outcome.ERROR
    elif verdict == "INCONCLUSIVE":
        outcome = Outcome.UNDETERMINED
    else:
        outcome = Outcome.REPORTED
    # ── 线索计数（35 §7 / B21）：**每条 lead 对应一次真实请求**。
    #
    # 之前是 `1 if REPORTED else 0`——那让 leads_closed/leads_total 恒等于
    # "有结论数/有结论数"，闭环率永远是 1.00，不携带任何信息（B12-closure 已记）。
    #
    # 现在的口径：judge 真读过的每个 pair 产一条 lead（那就是一次真请求），
    # 该 case 判出结论时这些 lead 一起闭环。于是：
    #   - 分母 = 本轮真实发出的 pair 数（>1，不再是恒等式）；
    #   - 分子 = 有结论的 case 的 pair 数；
    #   - 没跑出结论的 case（BLOCKED/INCONCLUSIVE）其 lead 留在 pending，
    #     **自然拉低闭环率**——这才是"没闭环"该有的样子。
    leads = len(result.pairs)
    leads_closed = leads if verdict in ("VERIFIED", "REFUTED") else 0
    # 渲染哈希的口径：**用绑定模板 + 本轮参数重算**，而不是"实际访问到的 path"。
    # 实际 path 里含服务器重写/尾斜杠等噪声，用它比对会把"能不能由 RouteSet 推出"
    # 变成"能不能由网络栈忠实回显"，那是另一回事。
    #
    # 真网络下 binding.template 含基址（解析期符号替换过），而 main() 的
    # template_hashes 来自 R.routeset()（相对形态）。两边口径必须一致，
    # 所以这里用**同一个来源的相对模板**来算，基址不进哈希。
    local_binding = R.binding_for(case.selector)
    rendered = local_binding.template
    params = _last_params(transport)
    for name, value in params.items():
        rendered = rendered.replace("{" + name + "}", value)
    path_sha = hashlib.sha256(rendered.encode()).hexdigest() if transport.sent else None
    LAST_PARAMS[case.case_id] = params

    return CaseResult(
        case_id=case.case_id,
        label=case.label,
        outcome=outcome,
        verdict=verdict,
        reason=result.reason,
        capsule_id=capsule_id,
        replay_status=replay_status,
        route_ref=local_binding.ref,
        template_sha256=local_binding.template_sha256,
        path_rendered_sha256=path_sha,
        steps=1 + len(result.pairs),
        # **0 而不是 800**：`scripted` 档没有任何 LLM 调用，真实 token 消耗就是 0。
        # 800 是凭空乘出来的数——10 §4 的口径是「全轮 token 总和」，把 0 写成 800
        # 会让「token 成本」这一列从指标变成装饰，而且没人看得出它是假的。
        # 等真接上模型（--profile llm）时，再从这里取 usage；现在如实记 0。
        tokens=0,
        # **实测**：真网络下这例真的花了多久（含起停服务）。
        # 进程内那版写死 0.01 是因为当时没有可测的东西；现在有了就不该再填常量——
        # 墙钟是这轮改造唯一的成本证据，填常量等于把证据换成装饰。
        elapsed_s=round(elapsed, 3),
        leads=leads,
        leads_closed=leads_closed,
    )


def _seal_round(results: list[CaseResult], directory: Path) -> list[CaseResult]:
    """一轮跑完：把攒下的 capsule 串成一条链落盘，并用**封印后**的 id 回填结论。

    为什么回填：``save_round_chain`` 会写 ``prev``，哈希随之改变；报告里若留着
    封印前的 ``capsule_id``，那条指针指向的文件在盘上不存在（B148 同款）。
    ``case_id`` 与 ``finding_id`` 的对应关系由 ``build_capsule`` 定死（``bench-{case}``），
    这里只照抄，不另造映射。
    """
    if not ROUND_DOCS:
        return results
    sealed = save_round_chain(ROUND_DOCS, directory)
    by_finding = {cap.finding_id: cap for cap in sealed}
    out: list[CaseResult] = []
    for r in results:
        cap = by_finding.get(f"bench-{r.case_id}")
        out.append(replace(r, capsule_id=cap.capsule_id) if cap is not None and r.capsule_id else r)
    return out


def findings_snapshot(rounds: list[RoundResult]) -> list[str]:
    """旧格式快照：向后再比一轮才叫"前后为空"。"""
    return findings_fingerprint(rounds)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--out", default="out/b12")
    #: 声明本轮有多少条覆盖记录来自 agent 自述（10 §3.0：必须为 0，非 0 整轮不算）
    ap.add_argument("--agent-reported", type=int, default=0)
    #: 上轮 finding 集合快照（B12.9）：重跑前后差必须为空，"补齐不改结论"
    ap.add_argument("--diff-findings", default="", help="上一轮导出的 finding 集合 json")
    #: ``https`` 时现场生成自签证书并只经 SSL_CERT_FILE 注入信任。
    #: **默认仍是 http**：https 是另一条独立验过的分支（test_transport_tls.py），
    #: 不是把 bench 偷偷换成更"像生产"的样子。
    ap.add_argument("--scheme", choices=("http", "https"), default="http")
    #: A8「模型不是瓶颈」：``none`` 表示连 scripted planner 都不用模型外壳，
    #: 纯条件表跑完，判定必须**逐字不变**。当前两种取值走的是同一条 scripted 路径
    #: （`ModelDecider.client` 注入点空，B18 之前没有真模型），
    #: 所以 `none` 与默认的差异只在**声明**上——这一点在 bench.md 里如实打印，
    #: 不假装"用了模型"。
    ap.add_argument("--model", choices=("scripted", "none"), default="scripted",
                    help="A8：none = 无模型（条件表）；判定必须与 scripted 逐字相同")
    args = ap.parse_args(argv)

    global TLS
    TLS = None
    if args.scheme == "https":
        import tempfile

        from lab.net_server import LOOPBACK
        from lab.tls import make_cert

        tls_dir = Path(tempfile.mkdtemp(prefix="vp-bench-tls-"))
        TLS = make_cert(tls_dir, host_ip=LOOPBACK)
        os.environ["SSL_CERT_FILE"] = str(TLS[0])
        print(f"TLS: 证书 {TLS[0]}（仅经 SSL_CERT_FILE 注入信任，未改系统信任库）")

    cases = load_cases(ROOT / "tests" / "bench" / "cases.yaml")
    fps = case_fingerprint(cases)
    rounds: list[RoundResult] = []
    template_hashes: dict[str, str] = {}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    # **每次运行一个证据目录**：capsule 是内容寻址的，同一个 r1/ 里跑第二次，
    # 上一轮的 41 份会和这一轮的混在一起——三份链头、验不过，而且报告说"123 份"
    # 时盘上可能是 369 份。目录带上本次运行的 id，报告里的份数才真的是盘上的份数。
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    capsules_root = out / "capsules" / run_id

    for n in range(1, args.rounds + 1):
        LAST_PARAMS.clear()  # 每轮重算：上一轮的参数不能冒充这一轮的
        ROUND_DOCS.clear()   # 每轮一条链：清空，别把上轮的文档接进这一轮的链
        ROUND_CLOCK[0] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        results = [run_case(c) for c in cases]
        results = _seal_round(results, capsules_root / f"r{n}")
        if args.agent_reported:
            # 显式声明：前 N 条覆盖记录按 agent 自述记账 → 硬门槛立刻红（不静默忽略）
            # CaseResult 是 frozen：用 replace 造新对象，别原地改
            results = [
                replace(r, coverage_src="agent_reported") if i < args.agent_reported else r
                for i, r in enumerate(results)
            ]
        rs = R.routeset()
        rounds.append(
            RoundResult(
                round_no=n,
                results=results,
                routeset_sha256=rs.routeset_sha256,
                model=args.model,
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
        param_values_by_case=LAST_PARAMS,
    )
    missed = [
        f"{r.case_id}（{r.reason or r.skipped_reason}）"
        for r in rounds[0].results
        if r.outcome in (Outcome.NOT_RUN, Outcome.ERROR)
    ]
    #: 有证据但重放对不上的用例：**点名**。只报一个数，那 6 条证据就成了噪声。
    unreproduced = [
        f"{r.case_id}（{r.replay_status}）"
        for r in rounds[0].results
        if r.capsule_id and r.replay_status not in ("OK", "NEVER")
    ]

    md = render_bench_md(rounds, gates=gates, case_fp=fps, missed=missed,
                         capsules_dir=str(capsules_root),
                         unreproduced=unreproduced)
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

    # B12.9：重跑前后 finding 集合差必须为空（补齐不改结论）
    fp_now = findings_fingerprint(rounds)
    (out / "findings.txt").write_text("\n".join(fp_now) + "\n", encoding="utf-8")
    if args.diff_findings:
        before = {
            line.strip()
            for line in Path(args.diff_findings).read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
        diff = set(fp_now) ^ before
        if diff:
            print(f"FINDINGS-DIFF FAIL：{len(diff)} 条变化（补齐改了结论，不许发布）")
            for line in sorted(diff)[:10]:
                print(f"  {line}")
            return 1
        print(f"findings_diff: OK（{len(fp_now)} 条，前后一致）")

    return 0 if (gates.ok and derivable.ok) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
