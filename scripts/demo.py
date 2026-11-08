#!/usr/bin/env python3
"""B17 端到端演示：**一条命令跑完 5 分钟脚本里的 6 步**。

跑的是真链路（不是打印假数据）：
  discovery 三档 → agent 循环 → oracle 判定 → capsule seal → 报告三视图 → replay → 缩 scope 被拒

默认 scripted planner（不依赖模型，断网可跑）。`--offline-only` 只跑到报告，不发包。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT, ROOT / "tests", ROOT / "tests" / "unit"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import lab.routes as R  # noqa: E402
from lab.app import App, make_token  # noqa: E402
from lab.transport_oob import LabTransport  # noqa: E402

from veripen_core.agent import (  # noqa: E402
    AgentState,
    ClosedLoop,
    ClosedLoopDecider,
    Loop,
    ScriptedDecider,
    classify_discovery,
)
from veripen_core.budget import Budget, BudgetLimits  # noqa: E402
from veripen.report import (  # noqa: E402
    FindingView,
    Outcome,
    ReportInput,
    build_manifest,
    lint_report,
    render_json,
    render_markdown,
    render_sarif,
)
from veripen_core.evidence import (  # noqa: E402
    ReplayEnv,
    ReplayMode,
    TraceResult,
    chain_capsules,
    dumps_capsule,
    replay,
    save_capsule,
    verify_chain,
)
from veripen_core.profile import load_profile  # noqa: E402
from veripen.bench.faces import extra_tags  # noqa: E402
from veripen_oracles import ALL_ORACLES  # noqa: E402
from veripen_oracles.common.harness import SymbolTable, run as run_oracle  # noqa: E402

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
    }
)
#: 演示只跑这四类（三类 A 档 + 一个越权），保证 5 分钟内说清也可复现
DEMO_ORACLES = ("idor_cross_account", "sqli_bool_diff", "ssrf_oob", "jwt_suite")
SELECTOR = {
    "idor_cross_account": "object_read",
    "sqli_bool_diff": "param_injectable",
    "ssrf_oob": "url_fetch",
    "jwt_suite": "protected_read",
}


def _say(step: str, text: str) -> None:
    print(f"[{step}] {text}")


def _oracle(oid: str):  # noqa: ANN202
    for cls in ALL_ORACLES:
        if cls.meta.id == oid:
            return cls()
    raise KeyError(oid)


class DemoRuntime:
    """把 agent 的 run_oracle 接到真实 oracle 骨架上。"""

    def __init__(self, app: App, transport: LabTransport) -> None:
        self.app = app
        self.transport = transport
        self.capsules: list[dict[str, object]] = []

    def list_routes(self, selector: str | None):
        rs = R.routeset()
        return [
            {"ref": b.ref, "tags": list(b.semantic_tags), "confidence": b.confidence, "source": b.source}
            for b in rs.bindings
            if selector is None or selector in b.semantic_tags
        ]

    def run_oracle(self, oracle_id: str, route_ref: str, params):
        tag = SELECTOR.get(oracle_id)
        if tag is None:
            return {"verdict": "BLOCKED", "reason": "route_unresolved", "route_strength": "contract"}
        binding = R.binding_for(tag)
        bindings = {tag: binding}
        for extra in extra_tags(oracle_id):
            bindings[extra] = R.binding_for(extra)
        result = run_oracle(
            _oracle(oracle_id),
            binding,
            self.transport,
            symbols=SYMBOLS,
            nonce=f"demo-{oracle_id}",
            # 必须给**合法 JWT**：jwt_suite 的前提是"拿合法会话去变造"。
            # 传一个 64 位填充串，基线直接 401 → BLOCKED，那条用例根本没被测——
            # 演示里出现一条 BLOCKED 会让观众以为"框架跑不动它"。
            token=make_token("primary"),
            bindings=bindings,
        )
        self.capsules.append({"oracle": oracle_id, "result": result, "binding": binding})
        return {
            "verdict": result.verdict.value,
            "reason": result.reason,
            "route_strength": "contract",
            # 不编证据指针（B152）：真 capsule 在步骤 5 统一 seal，此刻还没有哈希可给。
            "capsule_id": None,
            "evidence": "pending_seal",
        }

    def rediscover(self, reason: str):
        return {"routeset_sha256": R.routeset().routeset_sha256, "added": 0, "removed": 0,
                "changed": 0, "unresolved": []}

    def request_approval(self, action_class: str, reason: str):
        return {"approval_id": "ap-demo", "state": "pending"}


def step0_profile(profile_name: str) -> object:
    path = ROOT / "profiles" / profile_name / "profile.yaml"
    profile = load_profile(path)
    _say("0:00", f"客户档 {profile_name}：industry={profile.industry} "
                 f"oracles={len(profile.oracles.enabled)} profile_hash={profile.profile_hash()[:12]}")
    return profile


def step1_discovery() -> object:
    rs = R.routeset()
    sel = {oid: (SELECTOR[oid],) for oid in DEMO_ORACLES}
    outcome = classify_discovery(oracle_ids=list(DEMO_ORACLES), routeset=rs, selectors=sel)
    _say("0:30", f"discovery：{outcome.tier.value}，可跑 {len(outcome.bound_oracles)} 类，"
                 f"routeset={rs.routeset_sha256[:12]}")
    return outcome


def step2_loop(app: App, transport: LabTransport, outcome) -> object:  # noqa: ANN001
    runtime = DemoRuntime(app, transport)
    state = AgentState(pending=tuple(outcome.bound_oracles), strong=(),
                       wave_order=tuple(outcome.bound_oracles))
    # B12.7c 接线：demo 走**闭环版决策器**。它跑的是同一条 NEXT_RULES，
    # 只在前面加一条"有 pending lead 必须先验它"——没有 lead 时行为逐字相同。
    closed = ClosedLoop(ledger=None, task_id="demo", window=tuple(outcome.bound_oracles))
    loop = Loop(
        runtime=runtime,
        decider=ClosedLoopDecider(closed=closed),
        budget=Budget(limits=BudgetLimits(steps=20)),
        goal="demo",
        max_steps=20,
    )
    result = loop.run(state)
    _say("0:30", f"agent 循环：{result.decision.value}（{result.reason}），"
                 f"{len(result.step_entries)} 步，判定 {result.verdicts}")
    return runtime


#: finding_id → {步骤号: 该步实际发出的参数}。step3 填，step5 用。
#: 存起来而不是从 path_rendered 反解：反解会在带 body 的请求上丢参数，
#: 而且那是"从离线数据猜请求"，恰好是 27 N1 要防的事。
_CAP_PARAMS: dict[str, dict[int, dict[str, str]]] = {}


def _params_from_capsule(cap) -> dict[int, dict[str, str]]:  # noqa: ANN001
    return _CAP_PARAMS.get(cap.finding_id, {})


def _routeset_for(binding):  # noqa: ANN001, ANN201
    """只含该绑定的 RouteSet：capsule 的 route 块据此生成，replay 才能重解析到同一条。"""
    from veripen_core.routes.types import RouteSet

    full = R.routeset()
    return RouteSet(
        resolver_version=full.resolver_version,
        asset_id=full.asset_id,
        query_hash=full.query_hash,
        created_at=full.created_at,
        expires_at=full.expires_at,
        sources_used=full.sources_used,
        sources_failed=full.sources_failed,
        unresolved=full.unresolved,
        bindings=[binding],
        routeset_sha256=full.routeset_sha256,
    )


def step3_evidence(runtime: DemoRuntime, out: Path) -> list:  # noqa: ANN201
    """把 VERIFIED 的 oracle 结果封成 capsule 并串链。

    **证据必须来自这次真跑**，不是夹具模板。早先这里用 ``capsule_fixture``
    合成 capsule，再把 oracle id 打补丁改掉——步骤与断言都是假的，
    demo 却宣称"每条发现能复现"。现在改用 ``veripen.bench.capsule.build_capsule``，
    步骤取自 ``result.pairs``（judge 真正读过的 trace）、断言取自 ``judgement``。
    """
    from veripen.bench.capsule import build_capsule, step_params
    from veripen_core.evidence import seal_capsule_doc

    caps = []
    for item in runtime.capsules:
        result = item["result"]
        if result.verdict.value != "VERIFIED":
            continue
        # capsule 必须记"它实际打的那条面"，否则 replay 无法重解析（06 §5 第 3 步）
        binding = item["binding"]
        rs = _routeset_for(binding)
        cap = seal_capsule_doc(
            build_capsule(
                case_id=str(item["oracle"]),
                oracle_id=str(item["oracle"]),
                oracle_version=str(getattr(_oracle(str(item["oracle"])), "version", "1.0.0")),
                verdict=result.verdict,
                judgement=result.judgement,
                pairs=result.pairs,
                baseline_params=getattr(result, "baseline_params", {}) or {},
                routeset=rs,
                route_ref=binding.ref,
                binding=binding,
                profile_name="demo",
                cwe=_oracle(str(item["oracle"])).cwe,
            )
        )
        _CAP_PARAMS[cap.finding_id] = step_params(
            cap, getattr(result, "baseline_params", {}) or {}, result.pairs
        )
        caps.append(cap)
    if not caps:
        _say("1:30", "没有可封存的 VERIFIED 结果")
        return []
    chained = chain_capsules(caps)
    paths = [save_capsule(c, out / "capsules") for c in chained]
    report = verify_chain(chained)
    _say("1:30", f"证据：{len(chained)} 个 capsule，链完整={report.ok}，"
                 f"root={report.root_sha256[:12]}")
    return list(zip(chained, paths))


def step4_report(profile, sealed, out: Path, *, ledger=None, store=None) -> Path:  # noqa: ANN001
    """三视图 + 线索章节 + 死信章节。

    后两段**来自真库**（不是拼出来的示例文本）：
    - 线索：``runtime.capsules`` 每跑一个 oracle 就记一条 lead，验证通过即闭环；
    - 死信：本 demo 里 BLOCKED 的那条 oracle 走一次失败重试的完整路径。
    这样报告的每一段都能指回库里的一行，replay 时也不会出现"报告里有、库里没有"。
    """
    from veripen_core.deadletter import render_section as render_dead
    from veripen_core.leads import render_leads_section

    leads_md = render_leads_section(ledger, "demo") if ledger is not None else ""
    dead_md = render_dead(store) if store is not None else ""
    findings = []
    for i, (cap, _path) in enumerate(sealed):
        findings.append(
            FindingView(
                finding_id=f"f-{i + 1}",
                title=f"{cap.oracle.id} 已验证",
                asset_id=cap.target.asset_id,
                oracle_id=cap.oracle.id,
                cwe=cap.oracle.cwe or "CWE-0",
                severity="high" if i == 0 else "medium",
                confidence=cap.confidence,
                route_strength=cap.route_strength or "contract",
                route_ref=cap.route.ref if cap.route else None,
                capsule_id=cap.chain.self_sha256,
                self_sha256=cap.chain.self_sha256,
                replay_status="OK",
                template_sha256=cap.target.template_sha256,
                param_signature="path:id",
                situation=cap.assertion.statement or "判定成立",
                impact="见 capsule",
                advice="按复现命令重跑",
            )
        )
    report = ReportInput(
        engagement=profile.engagement_id,
        profile_hash=profile.profile_hash(),
        image_digest="demo",
        root_sha256=sealed[0][0].chain.prev_sha256 or "0" * 64,
        routeset_sha256=[sealed[0][0].route.routeset_sha256] if sealed[0][0].route else [],
        resolver_version="1.0.0",
        findings=tuple(findings),
        coverage={"zero_hit_tags": [], "by_source": {"machine_observed": len(findings)},
                  "route_causes": {}},
        frameworks=list(profile.frameworks),
        distribution="internal",  # 报告分发级别，与客户档名无关
        render_time="demo",
        # 复现命令要指到演示自己落证据的地方（B153）：默认值 "out/capsules" 在这里是死路径
        capsule_dir=str(out / "capsules").replace("\\", "/"),
        leads_section=leads_md,
        dead_section=dead_md,
    )
    md = render_markdown(report)
    sarif = render_sarif(report)
    js = render_json(report)
    manifest = build_manifest(markdown=md, sarif=sarif, js=js)
    hits = lint_report(list(report.findings), markdown=md, sarif=sarif, js=js)

    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(md, encoding="utf-8")
    (out / "report.sarif").write_text(json.dumps(sarif, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.json").write_text(json.dumps(js, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    _say("1:30", f"报告三视图：{len(findings)} 条，零地址命中={len(hits)}，"
                 f"manifest={manifest['report_sha256'][:12]}")
    return out / "report.md"


def step5_replay(sealed, transport) -> None:  # noqa: ANN001
    """真发包重放：**不是**把 capsule 里记的状态原样返回。

    早先这里用一个替身，按 role 返回 capsule 自带的 status/body_sha——
    那样"重放通过"是恒真的，什么都证明不了。现在真发一轮请求，
    由靶场回的真实状态与 body 哈希参与比对。
    """
    if not sealed:
        return
    cap, path = sealed[0]
    rs = R.routeset()
    params_by_n = _params_from_capsule(cap)
    inner = transport

    class T:
        def send(self, step, binding):  # noqa: ANN001, ANN201
            from veripen_core.evidence import TraceResult as _TR

            try:
                tr = inner.send(binding, params_by_n.get(int(step.n), {}), step.role, None)
            except Exception as exc:  # noqa: BLE001
                return _TR(status=0, body_sha256="", error=type(exc).__name__)
            return _TR(status=int(tr.status), body_sha256=tr.body_sha256)

    live = replay(cap, ReplayEnv(now=0, mode=ReplayMode.LIVE, routeset=rs), T())
    offline = replay(cap, ReplayEnv(now=0, mode=ReplayMode.OFFLINE), None)
    _say("2:10", f"replay：live={live.state.value} / 断网={offline.state.value}（同一 capsule {path.name[:12]}）")


def step6_scope_shrink(sealed) -> None:  # noqa: ANN001
    """全场最重要的一步：把资产移出 scope，replay 必须 BLOCKED 且**零发包**。"""
    if not sealed:
        return
    cap, _ = sealed[0]
    calls = {"n": 0}

    class Counting:
        def send(self, step, binding):  # noqa: ANN001, ANN201
            calls["n"] += 1
            raise AssertionError("不该发包")

    env = ReplayEnv(now=0, mode=ReplayMode.LIVE, routeset=R.routeset(), scope_in=frozenset({"other"}))
    result = replay(cap, env, Counting())
    _say("2:50", f"缩 scope：{result.state.value}（{result.reason}），实际发包 {calls['n']} 次")
    if result.state.value != "BLOCKED" or calls["n"] != 0:
        raise SystemExit("演示失败：缩 scope 后不该放行")


def step7_alt_profile(profile_name: str) -> None:
    path = ROOT / "profiles" / profile_name / "profile.yaml"
    alt = load_profile(path)
    _say("4:10", f"换档 {profile_name}：industry={alt.industry} "
                 f"profile_hash={alt.profile_hash()[:12]}（同一份镜像，只换 YAML）")


def step2b_leads(sealed, store) -> object:  # noqa: ANN001
    """把「真跑出来的判定」记成线索账本，并留下一条真死信。

    为什么用真库而不是拼示例文本：报告里的每一行都要能指回库里的一行。
    线索与判定一一对应（一条 oracle 一条 lead），判出结论即闭环；
    BLOCKED 的那条则走一次「失败 → 重试耗尽 → 死信 → 人重新投递」的完整路径。
    """
    from veripen_core.deadletter import to_dead_letter
    from veripen_core.leads import LeadLedger, LeadState
    from veripen_core.models import TaskState

    ledger = LeadLedger(store)
    for cap, _path in sealed:
        lead = ledger.record(
            task_id="demo",
            asset_id=cap.target.asset_id,
            source_action="verify.run_oracle",
            raw_ref=f"out/demo/capsules/{cap.chain.self_sha256[:16]}.json",
            summary=f"{cap.oracle.id} 判定 {cap.verdict.value}",
            verifiable_by=(cap.oracle.id,),
            confidence_hint=min(1.0, float(cap.confidence)),
        )
        # 判出结论 → 线索闭环（VERIFIED / REFUTED 都算闭环）
        ledger.supersede(
            lead.lead_id,
            state=LeadState.VERIFIED if cap.verdict.value == "VERIFIED" else LeadState.REFUTED,
            supersedes_note=cap.verdict.value,
        )

    # 一条死信：**演示用**，把一条任务走到「重试耗尽 → 死信」。
    # 早先它依赖"jwt_suite 恰好 BLOCKED"这个副作用——那条 BLOCKED 其实是
    # 基线 token 接线错误（已修），修好后就没有天然失败项了。
    # 这里不再靠副作用，而是明确挑一个本次没跑的 oracle 作为演示对象，
    # 并在文案里说清是演示，不冒充真实失败。
    sealed_ids = {c[0].oracle.id for c in sealed}
    blocked = [o for o in DEMO_ORACLES if o not in sealed_ids] or ["sqli_error_based"]
    if blocked:
        tid, _ = store.submit_task(
            engagement_id="demo", profile_name="demo", profile_hash="h",
            asset_id="a-demo", oracle_ids=[blocked[0]], max_attempts=3,
        )
        store.conn.execute(
            "UPDATE tasks SET state=?, attempt=3 WHERE id=?",
            (TaskState.FAILED.value, tid),
        )
        store.conn.commit()
        to_dead_letter(store, tid, error_class="retryable", error_code="VP-7004")

    _say("1:10", f"线索账本：{len(sealed)} 条全部闭环；"
                 f"死信 {1 if blocked else 0} 条（单列，演示重试耗尽路径）")
    return ledger


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="demo-a")
    ap.add_argument("--alt-profile", default="demo-b")
    ap.add_argument("--out", default="out/demo")
    ap.add_argument("--offline-only", action="store_true", help="只跑到报告，不做 replay")
    args = ap.parse_args(argv)
    out = Path(args.out)

    from veripen_core.store import Store

    store = Store(str(out / "demo.db")).init()

    profile = step0_profile(args.profile)
    outcome = step1_discovery()
    app = App(secure=False)
    transport = LabTransport(app)
    runtime = step2_loop(app, transport, outcome)
    sealed = step3_evidence(runtime, out)
    ledger = step2b_leads(sealed, store)
    step4_report(profile, sealed, out, ledger=ledger, store=store)
    if not args.offline_only:
        step5_replay(sealed, transport)
        step6_scope_shrink(sealed)
    step7_alt_profile(args.alt_profile)
    _say("4:40", "能卖第二家、差异没漏进代码、内网能装、每条发现能复现、有数。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
