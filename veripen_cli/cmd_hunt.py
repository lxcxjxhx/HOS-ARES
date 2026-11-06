#!/usr/bin/env python3
"""veripen hunt —— 对**真实目标**跑一次自动渗透（D-05 选项 B）。

与 `bench-leads` 的区别只有一条：`bench-leads` 打的是 `tests/lab` 的靶场，
这条命令打的是你在 `--target` 里给的那个地址。其余全同——同一批 oracle、
同一条守卫链、同一个 agent 循环、同样不产 verdict 之外的东西。

用法：

    python -m veripen_cli.cmd_hunt \\
        --target https://TARGET \\
        --openapi spec.json \\
        --model live \\
        --out out/hunt

三件事由**你**保证，命令不替你猜：

1. `--target` 的 host 必须出现在 `--openapi` 的 `servers` 里；
   契约写了别人的 host 会被 `parse_openapi` 直接拒（VP-1006）。
2. `--target` 的 host 同时进 `ScopeGuard.scope_in`——**不在许可面内的出网一律拒**。
3. 只读档：`impact_ceiling=READ_ONLY`，post_connect 仍校验对端 IP。

没有 `--skip-guard` 这类参数。要绕过守卫，只能改代码，改代码就在 diff 里。
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ._io import setup_stdout  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from veripen_contract.parser import ContractError, parse_openapi  # noqa: E402
from veripen_contract.planner import plan  # noqa: E402
from veripen_contract.spec_detect import SpecFormatError, as_openapi_text  # noqa: E402
from veripen_core.agent import AgentState, Loop, build_decider  # noqa: E402
from veripen_core.agent.client import from_env  # noqa: E402
from veripen_core.blackboard import AssetFacts  # noqa: E402
from veripen_core.briefing import build_knowledge, build_l1a, knowledge_text  # noqa: E402
from veripen_core.context import load_dir  # noqa: E402
from veripen_core.guard.budget import Budget  # noqa: E402
from veripen_core.guard.context import GuardContext  # noqa: E402
from veripen_core.guard.ratelimit import ThrottleConfig, TokenBucket  # noqa: E402
from veripen_core.guard.scope import AssetScope, PlanDecision, ScopeGuard  # noqa: E402
from veripen_core.store import Store  # noqa: E402
from veripen_core.routes import (  # noqa: E402
    AssetRef,
    Resolver,
    RouteHints,
    RouteProvenance,
    RouteQuery,
)
from veripen_core.routes.sources.symbols import (  # noqa: E402
    SymbolSource as SymSource,
    SymbolTable as RouteSymbols,
)
from veripen_core.routes.types import RouteSet  # noqa: E402
from veripen_core.tools import http as http_tool  # noqa: E402
from veripen_oracles.live import CoreTransport  # noqa: E402
from veripen_oracles import ALL_ORACLES  # noqa: E402
from veripen_oracles.common.harness import SymbolTable, run as run_oracle  # noqa: E402

#: 真实目标上的符号表。**默认是空的**：凡是非地址的字符串（OOB 域名、系统文件名、
#: 同行用户名）都必须由 operator 给，猜一个假的只会让结论看起来更"完整"。
#: 空表时 oracle 的 payload 填不出来，判定会落在 BLOCKED / INCONCLUSIVE——那是诚实的。

BASE_SYMBOL = "TARGET_BASE"


class HuntRuntime:
    """给 `Loop` 用的最小 runtime：只做 oracle 执行与重发现。"""

    def __init__(
        self, routeset: RouteSet, transport: CoreTransport, symbols: SymbolTable
    ) -> None:
        self.routeset = routeset
        self.transport = transport
        self.symbols = symbols
        self.calls: list[str] = []
        self.reasons: dict[str, str] = {}
        #: oracle_id → 它实际打的那条面。覆盖账本要按面归口，
        #: 而"挑了哪条绑定"这段逻辑只在这里发生过一次——不要在外面重算一遍。
        self.binding_refs: dict[str, str] = {}

    def list_assets(self) -> list[dict[str, Any]]:
        return [{"id": self.routeset.asset_id, "kind": "web", "criticality": "unknown"}]

    def list_routes(self, selector: str | None) -> list[dict[str, Any]]:
        return [
            {
                "ref": b.ref,
                "tags": list(b.semantic_tags),
                "confidence": b.confidence,
                "source": b.source,
            }
            for b in self.routeset.bindings
            if selector is None or selector in b.semantic_tags
        ]

    def run_oracle(self, oracle_id: str, route_ref: str, params: Any) -> dict[str, Any]:
        self.calls.append(oracle_id)
        cls = next((c for c in ALL_ORACLES if c.meta.id == oracle_id), None)
        if cls is None:
            self.reasons[oracle_id] = "unknown_oracle"
            return {"verdict": "BLOCKED", "reason": "unknown_oracle", "route_strength": "contract"}
        oracle = cls()
        tags = [str(t) for t in getattr(oracle.meta, "selector_tags", ())]
        bindings: dict[str, Any] = {}
        for tag in tags:
            for b in self.routeset.bindings:
                if tag in b.semantic_tags:
                    bindings[tag] = b
                    break
        if not tags or tags[0] not in bindings:
            self.reasons[oracle_id] = "route_unresolved"
            return {"verdict": "BLOCKED", "reason": "route_unresolved", "route_strength": "contract"}
        self.binding_refs[oracle_id] = bindings[tags[0]].ref
        self.transport.oracle_id = oracle_id
        try:
            result = run_oracle(
                oracle, bindings[tags[0]], self.transport,
                symbols=self.symbols, nonce=f"hunt-{oracle_id}", token=None, bindings=bindings,
            )
        except Exception as exc:  # noqa: BLE001 - 真实目标上什么都可能发生，如实报
            detail = f"{type(exc).__name__}: {exc}"[:160]
            self.reasons[oracle_id] = detail
            return {"verdict": "BLOCKED", "reason": detail, "route_strength": "contract"}
        self.reasons[oracle_id] = result.reason
        return {
            "verdict": result.verdict.value,
            "reason": result.reason,
            "route_strength": "contract",
            "capsule_id": f"sha256:{oracle_id}" if result.verdict.value == "VERIFIED" else None,
        }

    def rediscover(self, reason: str) -> dict[str, Any]:
        return {"routeset_sha256": self.routeset.routeset_sha256,
                "added": 0, "removed": 0, "changed": 0, "unresolved": [reason]}


def _read_secret(inline: str, file_path: str, env_name: str, label: str) -> str:
    """凭据读取：**文件 > 环境变量 > 命令行**。

    命令行排最后不是顺手写的：传在 argv 里的 token 会同时进 shell 历史、
    进程列表（`ps` 可见）与 CI 日志。文件和环境变量至少只泄漏一处。
    命令行这条路保留只为兼容，用了就告警。
    """
    if file_path:
        path = Path(file_path)
        if not path.is_file():
            raise SystemExit(f"{label} 文件不存在：{file_path}")
        value = path.read_text(encoding="utf-8").strip()
        if not value:
            raise SystemExit(f"{label} 文件是空的：{file_path}")
        return value
    env_value = os.environ.get(env_name, "").strip()
    if env_value:
        return env_value
    if inline.strip():
        print(
            f"[!] {label} 来自命令行：会进 shell 历史与进程列表，"
            f"改用 --*-file 或 {env_name}",
            file=sys.stderr,
        )
        return inline.strip()
    return ""


def _resolve_ips(host: str) -> frozenset[str]:
    """把目标域名解析成 IP 集合，填进 ``AssetScope.resolved_ips``。

    **必须填**：``check_post_connect`` 校验的是真实对端 IP，而域名本身不是 IP。
    scope 里给空集时，任何 peer 都会被判 ``ScopeViolation``——那看起来像"目标乱解析"，
    实际是我没告诉守卫"这个域名对应哪些 IP"。
    """
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        print(f"[!] 域名解析失败：{host}（{exc}）—— 出网会被守卫拒", file=sys.stderr)
        return frozenset()
    return frozenset(str(info[4][0]) for info in infos)


def _host_of(target: str) -> str:
    parts = urlsplit(target)
    if not parts.hostname:
        raise SystemExit(f"--target 没有主机名：{target}")
    if parts.scheme.lower() not in ("http", "https"):
        raise SystemExit(f"--target 只接受 http/https：{target}")
    return parts.hostname.lower()


def _build_routeset(target: str, candidates: Any, asset_id: str) -> RouteSet:
    src = SymSource(
        "contract", RouteProvenance.CONTRACT, candidates,
        RouteSymbols({BASE_SYMBOL: target.rstrip(chr(0x2F))}), base_symbol=BASE_SYMBOL,
    )
    q = RouteQuery(asset=AssetRef(id=asset_id), hints=RouteHints(), min_confidence=0.0)
    return Resolver(sources=[src]).resolve(q, now=int(time.time()))


def _oracles_for(routeset: RouteSet) -> tuple[str, ...]:
    """按 RouteSet 里真实存在的语义标签，挑出能跑的 oracle。"""
    present = {tag for b in routeset.bindings for tag in b.semantic_tags}
    picked: list[str] = []
    for cls in ALL_ORACLES:
        tags = getattr(cls.meta, "selector_tags", ())
        if tags and str(tags[0]) in present and cls.meta.id not in picked:
            picked.append(cls.meta.id)
    return tuple(sorted(picked))


def _load_l1a(args: Any, asset_id: str) -> Any:
    """打开跨任务记忆库并组装 L1a（`H11 B62`）。

    库不存在时用内存库走同一条代码路径——**不静默新建文件**，
    也让 ``AssetFacts.injectable()`` 在真实运行中始终被调用，而不是只在测试里。
    """
    db = Path(args.db) if getattr(args, "db", "") else Path(args.out) / "hunt.db"
    store = Store(str(db) if db.exists() else ":memory:").init()
    return build_l1a(AssetFacts(store), asset_id)


def build_guard(
    host: str,
    routeset: Any,
    oracles: tuple[str, ...],
    *,
    rate: float = 1.0,
    burst: float = 2.0,
) -> GuardContext:
    """装配出网闸门（`hunt` 与 `run` 共用）。

    **没有它任何出网请求都会抛 `GuardMissing`**——闸门不是可选项，
    所以两个入口必须走同一个构造函数，否则新入口会漏装。
    """
    resolved = _resolve_ips(host)
    scope = ScopeGuard(
        asset=AssetScope(id=host, host_keys=frozenset({host}), resolved_ips=resolved),
        scope_in=frozenset({host}),
        deny=frozenset(),
        min_confidence=0.0,
        now=int(time.time()),
    )
    return GuardContext(
        scope=scope,
        routeset=routeset,
        plan=PlanDecision(allowed_oracles=oracles),
        bucket=TokenBucket(ThrottleConfig(rate_per_s=rate, burst=burst)),
        budget=Budget(),
        now=int(time.time()),
    )


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="veripen hunt")
    ap.add_argument("--target", required=True, help="真实目标根地址（scheme + host[:port]）")
    ap.add_argument("--openapi", required=True, help="该目标的 OpenAPI 文档路径")
    ap.add_argument("--model", choices=("scripted", "live"), default="scripted")
    ap.add_argument("--token", default="",
                    help="[兼容] 主身份 Bearer token。会进 shell 历史与进程列表，优先用 --token-file")
    ap.add_argument("--token-file", default="", help="主身份 token 文件（单行，recommended）")
    ap.add_argument("--token-secondary", default="", help="[兼容] 第二身份 token")
    ap.add_argument("--token-secondary-file", default="", help="第二身份 token 文件")
    ap.add_argument("--oob", default="", help="OOB 域名（SSRF 类需要；不给则该类只能 INCONCLUSIVE）")
    ap.add_argument("--symbols", default="", help="符号表 JSON（SYSTEM_FILE / SAFE_TARGET 等）")
    ap.add_argument("--max-steps", type=int, default=20)
    #: 真实站点上的默认速率比靶场低一个量级。20 req/s 对着别人的业务就是压力测试，
    #: 而这条命令的定位是"验证"，不是"压测"。
    ap.add_argument("--rate", type=float, default=1.0, help="每秒请求上限（默认 1）")
    ap.add_argument("--burst", type=float, default=2.0, help="突发桶容量（默认 2）")
    #: 默认排掉三类：写操作、压力类。它们对真实目标有副作用或构成压测，
    #: 要开必须显式给 --exclude "" 并在报告里写清为什么。
    ap.add_argument(
        "--exclude",
        default="rate_limit_absent,mass_assignment,xss_stored_probe",
        help="逗号分隔的 oracle 黑名单；默认排除写类与压力类",
    )
    ap.add_argument("--out", default=str(ROOT / "out" / "hunt"))
    ap.add_argument(
        "--db",
        default="",
        help="跨任务记忆库（默认 <out>/hunt.db；库为空时 L1a 段为空，行为与未接线等价）",
    )
    ap.add_argument(
        "--knowledge-dir",
        default="",
        help="knowledge/*.md 目录；提供则装配 L2 段（≤2 条/步，永不进判定）",
    )
    args = ap.parse_args(argv)
    setup_stdout()

    host = _host_of(args.target)
    # 契约格式先归一（B89）：swagger 2.0 折成 OpenAPI 3，Postman/认不出的当场说清。
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

    excluded = {x.strip() for x in args.exclude.split(",") if x.strip()}

    planned = plan(opset, source=RouteProvenance.CONTRACT)
    routeset = _build_routeset(args.target, list(planned.candidates), host)
    oracles = tuple(o for o in _oracles_for(routeset) if o not in excluded)
    dropped = tuple(o for o in _oracles_for(routeset) if o in excluded)

    print(f"目标：{host}")
    print(f"契约：{len(planned.candidates)} 条候选 / 跳过 {len(planned.skipped)}")
    print(f"路由：{len(routeset.bindings)} 条绑定")
    print(f"可跑 oracle：{len(oracles)} 个 —— {', '.join(oracles) or '（无）'}")
    if dropped:
        print(f"已排除：{', '.join(dropped)}（写类/压力类，默认不动）")
    print(f"速率：{args.rate} req/s，突发 {args.burst}，上限 {args.max_steps} 步")
    if not oracles:
        print("契约里没有带语义标签的 operation，无法挑 oracle。")
        return 1

    tokens = {
        "primary": _read_secret(
            args.token, args.token_file, "VERIPEN_TARGET_TOKEN", "主身份 token"
        ),
        "secondary": _read_secret(
            args.token_secondary, args.token_secondary_file,
            "VERIPEN_TARGET_TOKEN_SECONDARY", "第二身份 token",
        ) or _read_secret(
            args.token, args.token_file, "VERIPEN_TARGET_TOKEN", "主身份 token"
        ),
    }
    symbols_map: dict[str, str] = {}
    if args.symbols:
        symbols_map.update(json.loads(Path(args.symbols).read_text(encoding="utf-8")))
    if args.oob:
        symbols_map["OOB"] = args.oob
        symbols_map["OOB_URL"] = "https" + ":" + "//" + args.oob
    if symbols_map:
        print(f"符号：{len(symbols_map)} 条（{', '.join(sorted(symbols_map))[:80]}）")
    if tokens["primary"]:
        print("凭据：已给主身份" + ("（含第二身份）" if tokens["secondary"] != tokens["primary"] else ""))

    transport = CoreTransport(
        token_for=lambda account: tokens.get(account, ""),
        timeout_s=10.0,
    )
    runtime = HuntRuntime(routeset, transport, SymbolTable(symbols_map))

    resolved = _resolve_ips(host)
    if resolved:
        print(f"解析：{len(resolved)} 个 IP —— {', '.join(sorted(resolved))}")
    scope = ScopeGuard(
        asset=AssetScope(id=host, host_keys=frozenset({host}), resolved_ips=resolved),
        scope_in=frozenset({host}),
        deny=frozenset(),
        min_confidence=0.0,
        now=int(time.time()),
    )
    ctx = GuardContext(
        scope=scope,
        routeset=routeset,
        plan=PlanDecision(allowed_oracles=oracles),
        bucket=TokenBucket(ThrottleConfig(rate_per_s=args.rate, burst=args.burst)),
        budget=Budget(),
        now=int(time.time()),
    )

    http_tool.install_guard(ctx)
    try:
        decider = build_decider(client=from_env() if args.model == "live" else None)
        state = AgentState(pending=oracles, strong=(), wave_order=oracles)
        l1a = _load_l1a(args, host)
        kctx = build_knowledge(
            docs=load_dir(args.knowledge_dir) if getattr(args, "knowledge_dir", "") else (),
        )
        ktext = knowledge_text(kctx)
        print(f"决策器：{decider.name}")
        if l1a.text:
            print(f"L1a：{len(l1a.used)} 条 / 降幅 {l1a.reduction:.0%} / "
                  f"过期 {len(l1a.stale_ids)} / 丢弃 {l1a.dropped}")
        else:
            print("L1a：空（库无可用跨任务事实）")
        print(f"L2 知识：{'已注入' if ktext else '空'}")
        print("-" * 78)
        goal = l1a.with_goal(f"验证 {host}")
        if ktext:
            goal = f"{goal}\n{ktext}"
        result = Loop(
            runtime=runtime, decider=decider,
            goal=goal, max_steps=args.max_steps,
        ).run(state)
    finally:
        http_tool.uninstall_guard()

    for e in result.step_entries:
        flag = "ok " if e.ok else "ERR"
        verdict = f" verdict={e.verdict}" if e.verdict else ""
        print(f"  {e.step:>2} {flag} {e.tool:<20} facts+{e.new_facts}{verdict}")
    print("-" * 78)
    print(f"决策：{result.decision.value}（{result.reason}）")

    findings = {k: v for k, v in result.verdicts.items() if v == "VERIFIED"}
    # 未确认的必须带 reason：只写 BLOCKED 等于说"没验出来"却不说为什么，
    # 复核者无法区分「目标没错」与「我发错了」（D1）。
    blocked = {
        k: {"verdict": v, "reason": runtime.reasons.get(k, "")}
        for k, v in result.verdicts.items() if v != "VERIFIED"
    }
    print(f"确认：{len(findings)} 条 —— {json.dumps(findings, ensure_ascii=False)}")
    if blocked:
        for k, v in blocked.items():
            print(f"未确认：{k} = {v['verdict']}（{v['reason']}）")

    # ── 覆盖账本（B87）：hunt 也要回答"漏了什么"，不能只有 run 有 ──────
    # 同一个账本、同一套缺口分类：换入口不等于换口径。
    from veripen_core.coverage import Observation, build_coverage, completion_status, write_coverage

    oracle_tags = {
        str(c.meta.id): tuple(str(t) for t in getattr(c.meta, "selector_tags", ()))
        for c in ALL_ORACLES
    }
    observations = [
        Observation(
            oracle_id=oid,
            route_ref=runtime.binding_refs.get(oid, ""),
            verdict=str(verdict),
            reason=runtime.reasons.get(oid, "")[:200],
        )
        for oid, verdict in result.verdicts.items()
    ]
    finished = completion_status(result.decision.value, result.reason) == "completed"
    coverage = build_coverage(
        asset_id=host,
        bindings=routeset.bindings,
        oracle_tags=oracle_tags,
        selected=oracles,
        observations=observations,
        excluded=sorted(excluded),
        run_status="completed" if finished else result.decision.value,
        exit_reason=result.reason,
    )

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    record = {
        "target_host": host,
        "candidates": len(planned.candidates),
        "skipped": list(planned.skipped),
        "bindings": len(routeset.bindings),
        "oracles": list(oracles),
        "decision": result.decision.value,
        "reason": result.reason,
        "findings": findings,
        "not_confirmed": blocked,
        "steps": [
            {"step": e.step, "tool": e.tool, "ok": e.ok,
             "error_class": e.error_class, "verdict": e.verdict}
            for e in result.step_entries
        ],
        "budget": dict(result.budget),
        "requests": transport.sent,
    }
    record["coverage"] = {
        "summary": coverage["summary"],
        "gaps": len(coverage["gaps"]),
        "complete": coverage["completeness"]["complete"],
    }
    (out / "hunt.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    cov_path = write_coverage(out, coverage)
    print()
    cov_summary = coverage["summary"]
    print(
        f"覆盖：{cov_summary['faces_covered']}/{cov_summary['faces']} 面"
        f" / 适用 {cov_summary['applicable_pairs']} 对 / 执行 {cov_summary['executed']}"
        f" / 缺口 {cov_summary['gaps']} → {cov_path.name}"
    )
    if not coverage["completeness"]["complete"]:
        for caveat in coverage["completeness"]["caveats"]:
            print(f"  注意：{caveat}")
    print(f"证据：{out / 'hunt.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
