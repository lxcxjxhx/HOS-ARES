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
from veripen_core.agent import AgentState, Loop, build_decider  # noqa: E402
from veripen_core.agent.client import from_env  # noqa: E402
from veripen_core.guard.budget import Budget  # noqa: E402
from veripen_core.guard.context import GuardContext  # noqa: E402
from veripen_core.guard.ratelimit import TokenBucket  # noqa: E402
from veripen_core.guard.scope import AssetScope, PlanDecision, ScopeGuard  # noqa: E402
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
            return {"verdict": "BLOCKED", "reason": "route_unresolved", "route_strength": "contract"}
        self.transport.oracle_id = oracle_id
        try:
            result = run_oracle(
                oracle, bindings[tags[0]], self.transport,
                symbols=self.symbols, nonce=f"hunt-{oracle_id}", token=None, bindings=bindings,
            )
        except Exception as exc:  # noqa: BLE001 - 真实目标上什么都可能发生，如实报
            return {
                "verdict": "BLOCKED",
                "reason": f"{type(exc).__name__}: {exc}"[:160],
                "route_strength": "contract",
            }
        return {
            "verdict": result.verdict.value,
            "reason": result.reason,
            "route_strength": "contract",
            "capsule_id": f"sha256:{oracle_id}" if result.verdict.value == "VERIFIED" else None,
        }

    def rediscover(self, reason: str) -> dict[str, Any]:
        return {"routeset_sha256": self.routeset.routeset_sha256,
                "added": 0, "removed": 0, "changed": 0, "unresolved": [reason]}


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


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="veripen hunt")
    ap.add_argument("--target", required=True, help="真实目标根地址（scheme + host[:port]）")
    ap.add_argument("--openapi", required=True, help="该目标的 OpenAPI 文档路径")
    ap.add_argument("--model", choices=("scripted", "live"), default="scripted")
    ap.add_argument("--token", default="", help="主身份 Bearer token（受保护面必须给）")
    ap.add_argument("--token-secondary", default="", help="第二身份 token（越权类需要两个身份）")
    ap.add_argument("--oob", default="", help="OOB 域名（SSRF 类需要；不给则该类只能 INCONCLUSIVE）")
    ap.add_argument("--symbols", default="", help="符号表 JSON（SYSTEM_FILE / SAFE_TARGET 等）")
    ap.add_argument("--max-steps", type=int, default=20)
    ap.add_argument("--out", default=str(ROOT / "out" / "hunt"))
    args = ap.parse_args(argv)
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
    oracles = _oracles_for(routeset)

    print(f"目标：{host}")
    print(f"契约：{len(planned.candidates)} 条候选 / 跳过 {len(planned.skipped)}")
    print(f"路由：{len(routeset.bindings)} 条绑定")
    print(f"可跑 oracle：{len(oracles)} 个 —— {', '.join(oracles) or '（无）'}")
    if not oracles:
        print("契约里没有带语义标签的 operation，无法挑 oracle。")
        return 1

    tokens = {
        "primary": args.token.strip(),
        "secondary": (args.token_secondary or args.token).strip(),
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

    scope = ScopeGuard(
        asset=AssetScope(id=host, host_keys=frozenset({host}), resolved_ips=frozenset()),
        scope_in=frozenset({host}),
        deny=frozenset(),
        min_confidence=0.0,
        now=int(time.time()),
    )
    ctx = GuardContext(
        scope=scope,
        routeset=routeset,
        plan=PlanDecision(allowed_oracles=oracles),
        bucket=TokenBucket(),
        budget=Budget(),
        now=int(time.time()),
    )

    http_tool.install_guard(ctx)
    try:
        decider = build_decider(client=from_env() if args.model == "live" else None)
        state = AgentState(pending=oracles, strong=(), wave_order=oracles)
        print(f"决策器：{decider.name}")
        print("-" * 78)
        result = Loop(
            runtime=runtime, decider=decider, goal=f"验证 {host}", max_steps=args.max_steps
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
    blocked = {k: v for k, v in result.verdicts.items() if v != "VERIFIED"}
    print(f"确认：{len(findings)} 条 —— {json.dumps(findings, ensure_ascii=False)}")
    if blocked:
        print(f"未确认：{json.dumps(blocked, ensure_ascii=False)}")

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
    (out / "hunt.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(f"证据：{out / 'hunt.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
