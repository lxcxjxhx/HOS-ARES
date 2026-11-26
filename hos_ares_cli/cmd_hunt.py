#!/usr/bin/env python3
"""hos-ares hunt —— 对**真实目标**跑一次自动渗透（D-05 选项 B）。

与 `bench-leads` 的区别只有一条：`bench-leads` 打的是 `tests/lab` 的靶场，
这条命令打的是你在 `--target` 里给的那个地址。其余全同——同一批 oracle、
同一条守卫链、同一个 agent 循环、同样不产 verdict 之外的东西。

用法：

    python -m hos_ares_cli.cmd_hunt \\
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
from typing import Any, Iterable
from urllib.parse import urlsplit

from ._discover_args import add_discovery_args
from ._io import read_text_arg, setup_stdout  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

#: 逐条路由摘要的上限：产物是给人看与机器读的，不是全量导出
BINDINGS_DETAIL_MAX = 200
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hos_ares_contract.parser import ContractError, parse_openapi  # noqa: E402
from hos_ares_contract.planner import plan  # noqa: E402
from hos_ares_contract.spec_detect import SpecFormatError, as_openapi_text  # noqa: E402
from hos_ares_core.agent import AgentState, Loop, build_decider  # noqa: E402
from hos_ares_core.agent.loop import MODEL_ERROR_TOOL  # noqa: E402
from hos_ares_core.agent.client import from_env  # noqa: E402
from hos_ares.bench.capsule import build_capsule  # noqa: E402
from hos_ares_core.evidence import ZERO_SHA, save_capsule, seal_capsule_doc  # noqa: E402
from hos_ares_core.blackboard import AssetFacts  # noqa: E402
from hos_ares_core.briefing import build_knowledge, build_l1a, knowledge_text  # noqa: E402
from hos_ares_core.context import load_dir  # noqa: E402
from hos_ares_core.guard.budget import Budget  # noqa: E402
from hos_ares_core.guard.context import GuardContext  # noqa: E402
from hos_ares_core.guard.ratelimit import ThrottleConfig, TokenBucket  # noqa: E402
from hos_ares_core.guard.scope import AssetScope, PlanDecision, ScopeGuard  # noqa: E402
from hos_ares_core.store import Store  # noqa: E402
from hos_ares_core.routes import (  # noqa: E402
    AssetRef,
    Resolver,
    RouteHints,
    RouteProvenance,
    RouteQuery,
)
from hos_ares_core.routes.sources.symbols import (  # noqa: E402
    SymbolSource as SymSource,
    SymbolTable as RouteSymbols,
)
from hos_ares_core.routes.types import RouteSet  # noqa: E402
from hos_ares_core.tools import http as http_tool  # noqa: E402
from hos_ares_oracles.live import CoreTransport  # noqa: E402
from hos_ares_oracles import ALL_ORACLES  # noqa: E402
from hos_ares_oracles.common.harness import SymbolTable, run as run_oracle  # noqa: E402

#: 真实目标上的符号表。**默认是空的**：凡是非地址的字符串（OOB 域名、系统文件名、
#: 同行用户名）都必须由 operator 给，猜一个假的只会让结论看起来更"完整"。
#: 空表时 oracle 的 payload 填不出来，判定会落在 BLOCKED / INCONCLUSIVE——那是诚实的。

BASE_SYMBOL = "TARGET_BASE"


def _strength_of(binding: Any) -> str:
    """面强度：与 run 共用同一条规则（routes.confidence.strength_of）。"""
    from hos_ares_core.routes.confidence import strength_of

    return strength_of(binding)


def _ref_tail(binding: Any, length: int = 8) -> str:
    """面标识的尾巴：``route://crawl/abcd1234…`` → ``abcd1234``。

    与 `cmd_run` 同口径：题头 ``route://`` 恰好 8 个字符，`ref[:8]` 会让
    同一个 oracle 的所有面拿到同一个 id。
    """
    tail = str(getattr(binding, "ref", "") or "").rsplit(chr(0x2F), 1)[-1]
    return tail[:length] or "noref"


#: 证据状态的一句话说明（进模型上下文）。只解释"为什么没有 id"，不替结论背书。
_EVIDENCE_NOTE = {
    "not_sealed": "这个 runtime 没接证据目录：这条结论没有可重放证据",
    "not_verified": "结论不是 VERIFIED，不落证据",
    "build_failed": "证据构造失败（原因见 reason），没有可重放证据",
}


class HuntRuntime:
    """给 `Loop` 用的最小 runtime：只做 oracle 执行与重发现。"""

    def __init__(
        self,
        routeset: RouteSet,
        transport: CoreTransport,
        symbols: SymbolTable,
        capsules_dir: Path | None = None,
    ) -> None:
        self.routeset = routeset
        self.transport = transport
        self.symbols = symbols
        self.calls: list[str] = []
        self.reasons: dict[str, str] = {}
        #: 证据落盘目录。**None = 不封 capsule**（测试与演示的默认）：
        #: 不封就明说，绝不给一个盘上不存在的 id（B152/B154）。
        self.capsules_dir = capsules_dir
        #: 本轮的链：第 i 条的 prev 是第 i-1 条的 self。增量封印，
        #: 于是工具结果拿到的是**终值**（不需要 B150 那种事后回填）。
        self._prev: str = ZERO_SHA
        self.sealed: list[str] = []
        #: 逐面证据：`{"oracle", "route_ref", "capsule_id"}`。
        #: **不能只按 oracle 记**——同一个 oracle 的多个面各有一份，按 oracle 存
        #: 最后一份会把"面 A 的发现"指到"面 B 的证据"（B148 的老坑，B155 修）。
        self.evidence: list[dict[str, str]] = []
        #: oracle_id → 它实际打的那条面。覆盖账本要按面归口，
        #: 而"挑了哪条绑定"这段逻辑只在这里发生过一次——不要在外面重算一遍。
        self.binding_refs: dict[str, str] = {}
        #: oracle_id → 下一条要跑的面下标（route_ref 没给时按序推进）
        self._cursor: dict[str, int] = {}
        #: 逐面运行记录：覆盖账本按面计，不再只记一条（形状与 run 共用 FaceRun）
        from hos_ares_core.coverage import FaceRun

        self.faces_run: list[FaceRun] = []

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

    def _seal_capsule(
        self, oracle: Any, chosen: Any, bindings: dict[str, Any], result: Any
    ) -> tuple[str | None, str]:
        """给 VERIFIED 结果落一份真证据；落不了就**明说**，绝不编 id（B154）。

        返回 ``(capsule_id, evidence_state)``：

        - ``("sha256:…", "sealed")``——盘上真的有一份，id 是终值（增量串链）
        - ``(None, "not_sealed")``——这个 runtime 没接证据目录（测试/演示）
        - ``(None, "build_failed:<kind>")``——证据造不出来，原因记进 ``reasons``
        """
        if self.capsules_dir is None:
            return None, "not_sealed"
        if result.judgement is None:
            return None, "build_failed:no_judgement"
        if not result.pairs:
            return None, "build_failed:no_pairs"
        try:
            doc = build_capsule(
                case_id=f"hunt-{oracle.meta.id}-{_ref_tail(chosen)}",
                oracle_id=str(oracle.meta.id),
                oracle_version=str(getattr(oracle.meta, "version", "1")),
                verdict=result.verdict,
                judgement=result.judgement,
                pairs=result.pairs,
                baseline_params=result.baseline_params,
                routeset=self.routeset,
                route_ref=str(chosen.ref),
                binding=chosen,
                bindings_by_tag=bindings,
                profile_name="hunt",
                created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                cwe=str(getattr(oracle.meta, "cwe", "")),
            )
            # 增量封印：prev 用上一条的 self，于是**这次返回的 id 不会再变**
            doc = seal_capsule_doc(doc, prev_sha256=self._prev)
            save_capsule(doc, self.capsules_dir)
        except Exception as exc:  # noqa: BLE001 —— 造不出来就如实记，换成假 id 才是事故
            kind = f"{type(exc).__name__}"
            self.reasons[str(oracle.meta.id)] = f"capsule_failed:{kind}:{str(exc)[:80]}"
            return None, f"build_failed:{kind}"
        self._prev = doc.chain.self_sha256
        self.sealed.append(doc.chain.self_sha256)
        self.evidence.append({
            "oracle": str(oracle.meta.id),
            "route_ref": str(chosen.ref),
            "capsule_id": doc.capsule_id,
        })
        return doc.capsule_id, "sealed"

    def run_oracle(self, oracle_id: str, route_ref: str, params: Any) -> dict[str, Any]:
        self.calls.append(oracle_id)
        cls = next((c for c in ALL_ORACLES if c.meta.id == oracle_id), None)
        if cls is None:
            self.reasons[oracle_id] = "unknown_oracle"
            return {"verdict": "BLOCKED", "reason": "unknown_oracle", "route_strength": "contract"}
        oracle = cls()
        tags = [str(t) for t in getattr(oracle.meta, "selector_tags", ())]
        faces = [
            b for b in self.routeset.bindings
            if tags and tags[0] in getattr(b, "semantic_tags", ())
        ]
        chosen = None
        if route_ref:
            # 模型指定了面就用它——**这个参数以前被完全忽略**
            chosen = next((b for b in faces if b.ref == route_ref), None)
            if chosen is None:
                self.reasons[oracle_id] = "route_ref_not_found"
                return {
                    "verdict": "BLOCKED",
                    "reason": "route_ref_not_found",
                    "route_strength": "none",
                    "faces_total": len(faces),
                }
        else:
            index = self._cursor.get(oracle_id, 0)
            if index >= len(faces):
                self.reasons[oracle_id] = "all_faces_done"
                return {
                    "verdict": "BLOCKED",
                    "reason": "all_faces_done",
                    "route_strength": "none",
                    "faces_total": len(faces),
                    "hint": "该 oracle 的全部面都已跑过；换 oracle 或指定 route_ref",
                }
            chosen = faces[index]
            self._cursor[oracle_id] = index + 1
        if chosen is None:
            self.reasons[oracle_id] = "route_unresolved"
            return {"verdict": "BLOCKED", "reason": "route_unresolved", "route_strength": "none"}
        bindings: dict[str, Any] = {
            tag: chosen for tag in tags if tag in getattr(chosen, "semantic_tags", ())
        }
        face_index = faces.index(chosen) + 1
        self.binding_refs[oracle_id] = chosen.ref
        self.transport.oracle_id = oracle_id
        try:
            result = run_oracle(
                oracle, chosen, self.transport,
                symbols=self.symbols, nonce=f"hunt-{oracle_id}", token=None, bindings=bindings,
            )
        except Exception as exc:  # noqa: BLE001 - 真实目标上什么都可能发生，如实报
            detail = f"{type(exc).__name__}: {exc}"[:160]
            self.reasons[oracle_id] = detail
            return {"verdict": "BLOCKED", "reason": detail, "route_strength": "contract"}
        self.reasons[oracle_id] = result.reason
        from hos_ares_core.coverage import FaceRun

        self.faces_run.append(FaceRun(
            oracle_id=oracle_id,
            route_ref=chosen.ref,
            template=str(getattr(chosen, "template", "")),
            verdict=result.verdict.value,
            reason=str(result.reason),
            face_index=face_index,
            faces_total=len(faces),
        ))
        capsule_id: str | None = None
        evidence = "not_verified"
        if result.verdict.value == "VERIFIED":
            capsule_id, evidence = self._seal_capsule(oracle, chosen, bindings, result)
        return {
            "verdict": result.verdict.value,
            "reason": result.reason,
            "face": str(getattr(chosen, "template", ""))[:80],
            "face_index": face_index,
            "faces_total": len(faces),
            # 面强度由置信档推：发现来的面是 crawl，不能跟着契约面一起写 contract
            "route_strength": _strength_of(bindings[tags[0]]),
            # **证据指针只能来自真的落盘的 capsule**（B152/B154）。封上了就是终值；
            # 封不上就说清是哪一步没成，不再有 `sha256:<oracle_id>` 那种假号。
            "capsule_id": capsule_id,
            "evidence": evidence,
            "evidence_note": _EVIDENCE_NOTE.get(
                "not_sealed" if evidence == "not_sealed" else evidence.split(":")[0],
                "",
            ),
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
    # 参数不合法属"输入有问题"（退出码 2），不是"跑完了"（1）——B342
    parts = urlsplit(target)
    if not parts.hostname:
        print(f"--target 没有主机名：{target}", file=sys.stderr)
        raise SystemExit(2)
    if parts.scheme.lower() not in ("http", "https"):
        print(f"--target 只接受 http/https：{target}", file=sys.stderr)
        raise SystemExit(2)
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


def _plan_decision(scope: ScopeGuard, oracles: Iterable[str],
                   ceiling: str) -> PlanDecision:
    """把 oracle 列表**过一遍闸门的 plan**（B187）。

    原先是 `PlanDecision(allowed_oracles=oracles)` 直接造出来——等于"我自己批准自己"：
    scope_in / deny / 影响顶棚 / 时间窗这几条判定从来没被调用过，
    于是 deny 配了也没用，`preflight()` 看到的永远是"全允许"。
    """
    from hos_ares_core.models import ImpactClass
    from hos_ares_oracles import ALL_ORACLES

    impact = {c.meta.id: c.meta.impact for c in ALL_ORACLES}
    return scope.plan(
        oracles,
        enabled_oracles=frozenset(oracles),
        impact_by_oracle=impact,
        asset_ceiling=ImpactClass(ceiling),
        in_window=True,
    )


def build_guard(
    host: str,
    routeset: Any,
    oracles: tuple[str, ...],
    *,
    rate: float = 1.0,
    burst: float = 2.0,
    deny: Iterable[str] = (),
    ceiling: str = "read_only",
    deny_nets: Iterable[str] = (),
) -> GuardContext:
    """装配出网闸门（`hunt` 与 `run` 共用）。

    **没有它任何出网请求都会抛 `GuardMissing`**——闸门不是可选项，
    所以两个入口必须走同一个构造函数，否则新入口会漏装。
    """
    resolved = _resolve_ips(host)
    # `deny` 里混着"资产 id"与"网段"两种写法（B311）：拆开，网段进 ④ 的对端校验。
    # 以前整串只按资产 id 比对，`10.0.0.0/8` 与 `DENY_SET_DEFAULT` 都静默失效。
    from hos_ares_core.guard.scope import split_deny

    deny_ids, nets_from_deny = split_deny(deny)
    scope = ScopeGuard(
        asset=AssetScope(id=host, host_keys=frozenset({host}), resolved_ips=resolved),
        scope_in=frozenset({host}),
        deny=deny_ids,
        deny_nets=nets_from_deny | frozenset(deny_nets),
        min_confidence=0.0,
        now=int(time.time()),
    )
    return GuardContext(
        scope=scope,
        routeset=routeset,
        plan=_plan_decision(scope, oracles, ceiling),
        bucket=TokenBucket(ThrottleConfig(rate_per_s=rate, burst=burst)),
        budget=Budget(),
        now=int(time.time()),
    )


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="hos-ares hunt")
    ap.add_argument("--target", required=True, help="真实目标根地址（scheme + host[:port]）")
    ap.add_argument("--openapi", default="", help="OpenAPI/Swagger 文档路径（与 --discover 二选一）")
    add_discovery_args(ap, include_enable=True, legacy_aliases=False)
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
    ap.add_argument("--deny", default="",
                    help="禁止触碰的主机（逗号分隔）；命中即整任务 BLOCKED，不发包")
    ap.add_argument("--max-impact", choices=("read_only", "reversible_write"),
                    default="read_only", help="影响顶棚（默认 read_only）")
    #: 默认排掉三类：写操作、压力类。它们对真实目标有副作用或构成压测，
    #: 要开必须显式给 --exclude "" 并在报告里写清为什么。
    ap.add_argument(
        "--exclude",
        default="rate_limit_absent,mass_assignment,xss_stored_probe",
        help="逗号分隔的 oracle 黑名单；默认排除写类与压力类",
    )
    ap.add_argument("--out", default=str(ROOT / "out" / "hunt"),
                    help="产物目录。证据目录只属于一轮：已有 capsule 的目录会被拒绝（B154）")
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
    # B538：与 `hos-ares run` 同一套标签采纳开关。以前只有 run 有，
    # 于是 hunt 路径上结构推断的标签**永远**进 proposed、永不采纳——
    # 同一份契约在两条命令下能跑的面不一样，这不是"口径差异"，是缺一个开关。
    ap.add_argument(
        "--confirm-structural",
        action="store_true",
        help="采纳 parser 的结构推断标签（source=structural，置信 0.80）；"
             "不带时只认契约显式标签",
    )
    ap.add_argument(
        "--confirm-tags",
        default="",
        help="逗号分隔的标签名：只采纳这些（来自 profile 的 selectors，即 operator 确认）",
    )
    args = ap.parse_args(argv)
    setup_stdout()

    host = _host_of(args.target)
    if bool(args.openapi) == bool(args.discover):
        print("--openapi 与 --discover 必须二选一", file=sys.stderr)
        return 2
    excluded = {x.strip() for x in args.exclude.split(",") if x.strip()}

    # 证据目录只属于一轮（B154）：hunt 现在会给 VERIFIED 封 capsule，
    # 同一目录跑第二次会把两轮的 capsule 混成两条链（B153 在 run 上修过的同一件事）。
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    capsules_dir = out / "capsules"
    stale = sorted(capsules_dir.glob("*.json"))
    if stale:
        print(
            f"拒绝重跑：{capsules_dir} 里已有 {len(stale)} 份 capsule。\n"
            "  证据是内容寻址的：同一目录跑两次会变成两条链，报告里的份数也对不上盘上。\n"
            "  换个 --out（例如 out/hunt-2），或先把上一轮移走（mv out/hunt out/hunt-1）。",
            file=sys.stderr,
        )
        return 2

    planned = None
    if args.discover:
        # 复用 run 的发现轮次：同一套地址纪律（绑定只能由 resolver 出），只是不封证据。
        from hos_ares_cli.cmd_run import _discover_routeset

        routeset = _discover_routeset(args, host)
    else:
        # 契约格式先归一（B89）：swagger 2.0 折成 OpenAPI 3，Postman/认不出的当场说清。
        raw_text = read_text_arg(args.openapi, what="契约文件")
        if raw_text is None:
            return 2
        try:
            text = as_openapi_text(raw_text)
        except SpecFormatError as exc:
            print(f"契约格式不支持：{exc}", file=sys.stderr)
            return 2
        try:
            opset = parse_openapi(text, asset_id=host, allowed_hosts=[host])
        except ContractError as exc:
            print(f"契约拒绝：{exc}", file=sys.stderr)
            return 2
        planned = plan(
            opset,
            source=RouteProvenance.CONTRACT,
            confirm={t.strip() for t in args.confirm_tags.split(",") if t.strip()},
            accept_structural=args.confirm_structural,
        )
        routeset = _build_routeset(args.target, list(planned.candidates), host)
    oracles = tuple(o for o in _oracles_for(routeset) if o not in excluded)
    dropped = tuple(o for o in _oracles_for(routeset) if o in excluded)

    print(f"目标：{host}")
    if planned is not None:
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
            args.token, args.token_file, "HOS_ARES_TARGET_TOKEN", "主身份 token"
        ),
        "secondary": _read_secret(
            args.token_secondary, args.token_secondary_file,
            "HOS_ARES_TARGET_TOKEN_SECONDARY", "第二身份 token",
        ) or _read_secret(
            args.token, args.token_file, "HOS_ARES_TARGET_TOKEN", "主身份 token"
        ),
    }
    symbols_map: dict[str, str] = {}
    if args.symbols:
        raw_symbols = read_text_arg(args.symbols, what="符号表")
        if raw_symbols is None:
            return 2
        try:
            symbols_map.update(json.loads(raw_symbols))
        except (json.JSONDecodeError, TypeError) as exc:
            print(f"符号表不是可用的 JSON 对象：{args.symbols}（{exc}）", file=sys.stderr)
            return 2
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
    runtime = HuntRuntime(routeset, transport, SymbolTable(symbols_map),
                          capsules_dir=capsules_dir)

    resolved = _resolve_ips(host)
    if resolved:
        print(f"解析：{len(resolved)} 个 IP —— {', '.join(sorted(resolved))}")
    # **只走 `build_guard` 这一个构造点**（B335）：以前这里 inline 又建了一个 ScopeGuard，
    # 于是 B311 修好 run 的 deny 拆分后，hunt 这一路仍旧惰性——同一个对象两处构造，
    # 修一处等于只修一半。
    ctx = build_guard(
        host, routeset, oracles,
        rate=args.rate, burst=args.burst,
        deny=[x.strip() for x in args.deny.split(",") if x.strip()],
        ceiling=args.max_impact,
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

    model_errors = []
    for e in result.step_entries:
        flag = "ok " if e.ok else "ERR"
        verdict = f" verdict={e.verdict}" if e.verdict else ""
        # B238：`--model live` 失败时这里原本直接打内部哨兵 `__model_error__`，
        # 客户看到一行下划线开头的名字，既不知道发生了什么事，也不知道该动哪里。
        label = "模型调用失败" if e.tool == MODEL_ERROR_TOOL else e.tool
        if e.tool == MODEL_ERROR_TOOL:
            model_errors.append(e)
        print(f"  {e.step:>2} {flag} {label:<20} facts+{e.new_facts}{verdict}")
    if model_errors:
        classes = {str(e.error_class or "") for e in model_errors}
        why = "模型端点不可达/报错" if "transport" in classes else "模型输出不合窄 schema"
        print(f"注意：{len(model_errors)} 步没拿到可用调用（{why}）——已降级到 scripted 继续；"
              "判定结果不受影响，但这几步没参与决策")
        if "transport" in classes:
            print("      `hos-ares doctor` 的「模型后端」项可复现")
    print("-" * 78)
    print(f"决策：{result.decision.value}（{result.reason}）")
    # 模型自述：**展示，但不计入判定**（B158）。早先这段文本被整个丢掉，
    # 于是"我说了三条没测"和"我什么都没说"在产物里一模一样。
    if result.agent_summary:
        print(f"自述（不计入判定）：{result.agent_summary[:160]}")
    if result.agent_unresolved:
        print(f"自述未解决：{'、'.join(result.agent_unresolved[:8])}")

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
    from hos_ares_core.coverage import Observation, build_coverage, completion_status, write_coverage

    oracle_tags = {
        str(c.meta.id): tuple(str(t) for t in getattr(c.meta, "selector_tags", ()))
        for c in ALL_ORACLES
    }
    # 有逐面记录就按面计（多面执行），否则退回"一 oracle 一条"的老口径
    if runtime.faces_run:
        from hos_ares_core.coverage import observations_from

        observations = observations_from(runtime.faces_run)
    else:
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
        # 无契约路径没有"契约候选"这个概念：给 None 而不是 0——0 会被读成"契约里 0 条"
        "candidates": (len(planned.candidates) if planned is not None else None),
        "skipped": (list(planned.skipped) if planned is not None else None),
        "source": ("discover" if planned is None else "contract"),
        "bindings": len(routeset.bindings),
        # 逐条摘要（B213）：只记条数的话，控制台的"路由来源"页永远是空表——
        # 想看一眼本轮解析出哪些路由，只能重跑。**模板正文仍不落盘**（22 §2），
        # 这里只有 ref / 标签 / 来源 / 置信度 / ttl / 模板指纹。
        "bindings_detail": [
            {
                "ref": b.ref,
                "source": str(getattr(b.source, "value", b.source)),
                "confidence": round(float(b.confidence), 3),
                "ttl_s": b.ttl_s,
                "template_sha256": b.template_sha256,
                "semantic_tags": list(b.semantic_tags),
            }
            for b in routeset.bindings[:BINDINGS_DETAIL_MAX]
        ],
        "oracles": list(oracles),
        # 排除要留痕（B329）：终端打了"已排除：…"，而 hunt.json / hunt.md 里一个字都没有，
        # 读者看到"可跑 2 个"不知道原本有 5 个。
        "excluded": sorted(excluded),
        "decision": result.decision.value,
        "reason": result.reason,
        # 决策词是 `converged` 时读起来像"正常收敛"，而本轮可能一面都没判成（B334）
        "blocked_note": (
            f"本轮 {len(ctx.scope.violations())} 次被边界挡下，没有一面判成——"
            "决策词不等于「正常收敛」，看 bounds.json"
            if (not findings and ctx.scope.violations()) else ""
        ),
        "findings": findings,
        # 模型自述（B158）：与 findings/faces_run 分列——自述**不能**算覆盖，
        # 但把它丢掉等于让模型白说。两者并列，读者自己判断。
        "agent_claim": {
            "summary": result.agent_summary,
            "unresolved": list(result.agent_unresolved),
        },
        # 证据：这一轮**真的落盘**的 capsule（B154）。空列表 = 没有一份证据，
        # 不是"证据被省略"——报告读者据此就知道哪些结论能被重放。
        # `evidence` 是**逐面**的（同一 oracle 不同面各一份）；`capsules` 只是自哈希清单。
        "capsules": list(runtime.sealed),
        "evidence": list(runtime.evidence),
        # 逐面运行记录：多面执行时"一个 oracle 一条"已经不够描述
        "faces_run": [row.as_dict() for row in runtime.faces_run],
        "not_confirmed": blocked,
        "steps": [
            # 机器产物里也别留内部哨兵名（B258）：`__model_error__` 是 loop 的实现细节，
            # 下游按 tool 名做统计/告警的人不该被迫认这个名字。对外统一叫 `model_error`。
            {"step": e.step,
             "tool": "model_error" if e.tool == MODEL_ERROR_TOOL else e.tool,
             "ok": e.ok, "error_class": e.error_class, "verdict": e.verdict}
            for e in result.step_entries
        ],
        "budget": dict(result.budget),
        "requests": transport.sent,
    }
    record["coverage"] = {
        "summary": coverage["summary"],
        "gaps": len(coverage["gaps"]),
        "complete": coverage["completeness"]["complete"],
        # 为什么"不完整"要跟着走：报告里只写"完整：否"而不给原因，读者会以为还有面没跑（B166）
        "caveats": list(coverage["completeness"]["caveats"]),
    }
    # 与 run 同口径（B319）：落盘字符串固定 LF、清单逐文件哈希——客户能 `sha256sum` 核。
    from hos_ares.report.lint import dumps_artifact

    # 越界事件落盘（B334）：run 那一路有 bounds.json，hunt 没有——同样会被边界挡下，
    # 却没有可核的痕迹。写出来，并纳进清单。
    violations = ctx.scope.violations()
    bounds_text = dumps_artifact({"count": len(violations), "events": violations})
    (out / "bounds.json").write_text(bounds_text, encoding="utf-8", newline=chr(10))

    # 能力边界（B400）：`hunt.md` 有、`hunt.json` 没有——机器消费者读到的仍是"全测过了"。
    # 与 run/hunt/platform 同一份实现，字段形状与 `findings.json` 对齐。
    from hos_ares.report.limits import capability_limits  # noqa: PLC0415

    record["capability_limits"] = [
        {"kind": kind, "check": check_id, "why": why}
        for kind, rows in capability_limits().items()
        for check_id, why in rows
    ]
    hunt_text = dumps_artifact(record)
    (out / "hunt.json").write_text(hunt_text, encoding="utf-8", newline=chr(10))
    # 可交付报告（B166）：与 hunt.json **同源渲染**，同一个 dict 进两份产物
    from hos_ares.report.hunt import render_hunt_markdown

    # 报告里的证据路径用**相对 `--out`** 的形式（B191）：与 run 的报告同口径
    cap_rel = "capsules"
    report_path = out / "hunt.md"
    md_text = render_hunt_markdown(
        record,
        # 相对 `--out`（B191）：与 run 的报告同口径，绝对路径换台机器就读不出来
        capsules_dir=cap_rel,
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    )
    report_path.write_text(md_text, encoding="utf-8", newline=chr(10))
    # 清单（B319）：hunt 之前**没有**清单，于是"这两份产物没被手改"无从证明。
    import hashlib as _hashlib

    md_sha = _hashlib.sha256(md_text.encode("utf-8")).hexdigest()
    js_sha = _hashlib.sha256(hunt_text.encode("utf-8")).hexdigest()
    manifest = {
        "version": 1,
        "sections": {"markdown": md_sha, "json": js_sha},
        "files": {
            "hunt.md": md_sha,
            "hunt.json": js_sha,
            "bounds.json": _hashlib.sha256(bounds_text.encode("utf-8")).hexdigest(),
        },
        "report_sha256": _hashlib.sha256((md_sha + js_sha).encode("utf-8")).hexdigest(),
    }
    # 清单自哈希（B321）：与 run 那一路同形
    import hashlib as _h2

    manifest["manifest_sha256"] = _h2.sha256(
        dumps_artifact(manifest).encode("utf-8")).hexdigest()
    (out / "hunt.manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8", newline=chr(10))
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
    print(f"证据：{len(runtime.sealed)} 份 capsule → {capsules_dir}")
    print(f"产物：{out / 'hunt.json'} / {out / 'hunt.manifest.json'}")
    print(f"报告：{report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
