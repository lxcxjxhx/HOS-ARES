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
import atexit
import socket
import sys
import time
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]

#: OOB 监听只绑环回。这是全仓**唯一**允许出现环回字面量的位置（noroute-ok 显式豁免）——
#: 它描述的是"我要监听哪里"，不是任何目标地址。
LOOPBACK_HOST = "127.0.0.1"  # noroute-ok: OOB 监听绑定地址，非目标
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
from ._io import read_text_arg, setup_stdout  # noqa: E402
from .cmd_hunt import _build_routeset, _host_of, _oracles_for, build_guard  # noqa: E402

#: 证据目录名（相对 --out）
#: 无可跑 oracle 的三种原因 → 各自该做什么（B278）
_NO_ORACLE_NEXT = {
    "no_tag": "这些面推不出类型。给契约（--openapi），或让发现阶段能从参数名/响应形状认出类型",
    "no_oracle": "这些标签目前没有对应 oracle。换一类面，或看 coverage 里还缺哪些面",
    "excluded": "对应 oracle 被 --exclude 排除了。去掉那条排除，或确认它确实该排除",
}

CAPSULE_DIR = "capsules"

#: 默认排除：写类与压力类（与 hunt 同口径）。
#:
#: 原来是 `rate_limit_missing`——**没有这个 oracle**（真名 `rate_limit_absent`）。
#: 于是一条 DEFAULT_OFF 的类从来没被默认排除：只要某条面被打上 `rate_sensitive`
#: 标签，它就会被默认跑起来（那是压力类动作，正是默认关闭要拦的）。B179。
DEFAULT_EXCLUDE = "rate_limit_absent,mass_assignment,xss_stored_probe"


def _meta(oracle: Any, key: str, default: Any = "") -> Any:
    """读 oracle 元信息。**同时接受 oracle 与 oracle.meta 两种传法。**

    早期实现写的是 ``getattr(getattr(oracle, "meta", None), key, default)``，
    而调用方传进来的已经是 ``oracle.meta``——再取一次 ``.meta`` 得到 ``None``，
    于是**每一次**都落到默认值：severity 恒 medium、cwe 恒空、标题恒为 oracle id。
    报告看起来"有内容"，但每一条的严重度都是编出来的。
    """
    meta = getattr(oracle, "meta", oracle)
    return getattr(meta, key, default)


def _show(path: str | Path) -> str:
    """尽量用相对 cwd 的形式显示路径（B303）：绝对路径会把机器布局写进终端输出。"""
    try:
        return Path(path).relative_to(Path.cwd()).as_posix()
    except ValueError:
        return str(path)


def _next_out(current: str) -> str:
    """给"换个 --out"举一个**贴着用户当前路径**的例子（B303）。

    能在 cwd 下表示就写相对路径：绝对路径会把操作员的机器布局写进终端输出
    （与 B189/B247 对交付物的口径一致）。
    """
    path = Path(current)
    nxt = path.with_name(path.name + "-2")
    try:
        return nxt.relative_to(Path.cwd()).as_posix()
    except ValueError:
        return str(nxt)


def _trace_chain(result: Any) -> tuple[dict[str, Any], ...]:
    """三条件观测链：`baseline` 一次 + 每个变体一次（角色/状态/字节数）。

    只放**元数据**，不放正文：正文在 capsule 里，报告不复制第二份（B374）。
    没有 pairs（例如提前 BLOCKED）就给空——SARIF 那边据此不写 codeFlows，
    而不是编一段空证据链。
    """
    pairs = tuple(getattr(result, "pairs", ()) or ())
    if not pairs:
        return ()
    def _one(role: str, trace: Any) -> dict[str, Any]:
        return {
            "role": role,
            "status": int(getattr(trace, "status", 0) or 0),
            "bytes": len(getattr(trace, "body", b"") or b""),
        }
    chain = [_one("baseline", pairs[0].baseline)]
    for pair in pairs:
        chain.append(_one(str(pair.role), pair.trace))
    return tuple(chain)


def _findings_view(
    *, asset_id: str, oracle_id: str, meta: Any, result: Any,
    binding: Any, capsule: CapsuleDoc | None, observes_failures: bool = False,
) -> Any:
    """一次执行 → 渲染用 finding。**verdict 只来自 result，不在这里改。**"""
    from veripen.report.impact import advice_text, impact_text
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
    # 传输层失败 = 没跑成（error），不是"跑了但判不了"（B299）：
    # verdict 只有四值，没有 ERROR，所以在映射这一层按原因码纠回来。
    if str(getattr(result, "reason", "") or "").startswith("transport_error:"):
        outcome = Outcome.ERROR
    # **一个请求都没跑成** = 没测成（error），不是"跑了但判不了"（B385 实证）：
    # TLS/连接层失败现在被记成 `trace.error`（不再是异常冒到顶），于是
    # 目标整台不可达时 `verdict=INCONCLUSIVE`、四态落到 undetermined，
    # `run` 的退出码判据（verified==0 且 errors/blocked>0）看不到它，CI 会当成绿。
    pairs = list(getattr(result, "pairs", ()) or ())
    # 但**观测失败本身就是判据**的 oracle（证书/TLS 拒绝）除外：它的所有请求都带 error
    # 是正常现象，记成 error 就把"看见了缺陷"读成"工具崩了"（B393）。
    if (pairs and all(getattr(p.trace, "error", None) for p in pairs)
            and not observes_failures):
        outcome = Outcome.ERROR
    # 边界拒绝 = 未执行（授权/跳过那一类），不是"跑了但判不了"（B311）
    from veripen.report.glossary import SCOPE_CODES

    if str(getattr(result, "reason", "") or "") in SCOPE_CODES:
        outcome = Outcome.NOT_RUN
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
        # 影响/建议由**已声明的元数据**生成（B217）：以前这两行是空的，
        # 渲染器按"空就不写"处理，交付物里就没有影响与建议。
        impact=impact_text(getattr(meta, "impact", "")),
        advice=advice_text(_severity_of(meta, oracle_id, _strength_of(binding))[0]),
        outcome=outcome,
        refuted=verdict == "REFUTED",
        reason_code=str(getattr(result, "reason", "") or "")[:60],
        traces=_trace_chain(result),
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
        # B236：`fetched` 计的是**尝试数**（预算是按它扣的）——整台不可达时它照样加 1，
        # 所以"抓了 N 页"要同时给"拿到响应 M 页"，否则和下一行"一页都没抓成"互相打脸。
        print(f"发现第 {round_no} 轮：尝试 {step.fetched} 页 / 拿到响应 {step.responded} 页"
              f" / 新面 {fresh} 条")
        # 抓了却一条都没成，必须说清为什么（B221）：否则下一句"没有可跑的 oracle"
        # 会把"目标连不上"读成"目标没有可测的面"，用户会去改错的东西
        if step.fetched and not step.records:
            # 日志被管道接走时 stdout 是块缓冲、stderr 是无缓冲：不先 flush，
            # 这段解释会跑到它解释的那一行**前面**（CI 日志里看就是倒序）。
            sys.stdout.flush()
            reasons: dict[str, int] = {}
            for _tpl, why in getattr(step, "skipped", ()):
                reasons[str(why)] = reasons.get(str(why), 0) + 1
            detail = "、".join(f"{k}×{v}" for k, v in sorted(reasons.items())) or "未记录原因"
            # 「没拿到响应」与「拿到了但不是能测的页」是两件事（B277）：
            # 前者去查网络，后者去查目标是不是 404/非 HTML。混成一句话，
            # 上面刚说的"拿到响应 3 页"就在同一屏里自相矛盾。
            if step.responded:
                print(f"  拿到响应，但没有可解析的页：{detail}", file=sys.stderr)
                print("  404/非 HTML 都算这一类——目标在线，只是没给能测的面：",
                      file=sys.stderr)
            else:
                print(f"  这一轮一页都没抓成：{detail}", file=sys.stderr)
            if set(reasons) == {"fetch_failed"}:
                print("  连接层面就没过——先确认目标可达（端口/证书/代理）再谈覆盖面：",
                      file=sys.stderr)
                # 用**用户原样给的 target**：`host` 只是主机名，丢了端口时那条提示
                # 会指到默认端口上（实测 127.0.0.1:1 被印成 127.0.0.1）
                probe = str(getattr(args, "target", "") or host).rstrip(SEP)
                print(f"  可试：curl -sS -o NUL -w '%{{http_code}}' {probe}", file=sys.stderr)
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
    """现况一句话。**严重度的来路写在这里**，而不是靠读者猜 medium 是评估还是兜底。

    文字取 ``assertion.statement``（oracle 自己写的那句）。**不拿 ``reason`` 当句子**（B245）：
    ``reason`` 是判定码（``nosniff_absent`` / ``boolean_differential_holds``），
    它已经单独进了 ``reason_code`` 字段；塞进"现象"就会让交付报告里出现一串内部标识符——
    实测报告里 `- 现象：csp_header_absent` 这种行有一整排。
    """
    strength = _strength_of(binding)
    _severity, basis = _severity_of(meta, oracle_id, strength)
    prefix = ""
    if basis == "weak_face_cap":
        prefix = f"弱面（{strength}）：严重度按 medium 计。"
    elif basis == "default":
        prefix = "oracle 未声明该规则对应的严重度，按 medium 兜底。"
    from collections.abc import Mapping as _Mapping

    reason = str(getattr(result, "reason", "") or "")
    assertion = getattr(result, "assertion", None)
    statement = ""
    if isinstance(assertion, _Mapping):
        statement = str(assertion.get("statement") or "").strip()
    if statement:
        body = statement
    elif reason:
        # 没写文字就别编：如实说这一条只有判定码，读的人才知道该去看 capsule
        body = f"该 oracle 未给出文字现象（判定码 {reason}）"
    else:
        body = ""
    # 一句话都没有时连严重度前缀也不留：只有前缀的"现象"是一句悬空的话
    return (prefix + body)[:200] if body else ""


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="veripen run", description="端到端渗透：跑完给交付物")
    ap.add_argument("--target", required=True, help="目标根地址（scheme + host[:port]）")
    ap.add_argument("--openapi", default="", help="OpenAPI/Swagger 文档路径（与 --discover 二选一）")
    add_discovery_args(ap, include_enable=True, legacy_aliases=False)
    ap.add_argument("--out", default=str(ROOT / "out" / "run"),
                    help="产物目录。证据目录只属于一轮：已有 capsule 的目录会被拒绝（B153）")
    ap.add_argument("--exclude", default=DEFAULT_EXCLUDE, help="逗号分隔的 oracle 黑名单")
    # 审计库（B339）：平台那一路有，run 没有——于是"这一轮做了什么"只能靠 stdout 回忆。
    ap.add_argument("--db", default="", help="事件库（给了就把本轮事件落库，并在报告里附审计五表）")
    ap.add_argument("--token", default="", help="主身份 token（可空；空则相关 oracle 落 BLOCKED）")
    # 与 hunt 对齐（B378）：以前 run 只有主身份、没有 OOB，于是 `auth:secondary` /
    # `oob:listener` 两类 requires 在主命令里永远满足不了（hunt 那条路早就支持）。
    ap.add_argument("--token-secondary", default="",
                    help="第二身份 token（越权/双身份类需要；不给则这类只能 BLOCKED）")
    ap.add_argument("--token-secondary-file", default="",
                    help="第二身份 token 文件（单行；比命令行安全）")
    ap.add_argument("--oob", default="",
                    help="OOB 域名（SSRF 类需要；不给则该类只能 INCONCLUSIVE）")
    ap.add_argument("--oracle-params", default="",
                    help='按 oracle 传参数（JSON）：如 {\"mtls_required\": {\"expect_mtls\": true}}')
    ap.add_argument("--oob-listen", action="store_true",
                    help="本机起一个回环监听收真实回调（SSRF 类）；端口由系统分配")
    # 符号表（B379）：靶场与 bench 一直靠 `STRANGER=secondary`、`DEAD_HOST=…` 这类符号，
    # 但主命令没有入口——`--token-secondary` 给了凭据，oracle 却仍在 `symbols.resolved`
    # 上撞 `symbol_missing:STRANGER`。两个入口必须同时存在才算"给得进"。
    ap.add_argument("--symbols", default="",
                    help="符号表（JSON 对象或文件）：如 {\"DEAD_HOST\": \"x.invalid\"}")
    ap.add_argument("--symbols-file", default="",
                    help="符号表 JSON 文件（单行或多行对象）")
    ap.add_argument(
        "--strict-requires", action="store_true",
        help="按 07 §321 执行 requires：缺输入即 BLOCKED，且一个请求都不发（默认关，见账本 B163/B173）",
    )
    ap.add_argument("--max-oracles", type=int, default=0, help="0 = 全跑")
    ap.add_argument(
        "--frameworks", default="",
        help="合规框架（逗号分隔，如 owasp_wstg,cwe）；给了才出「合规映射」一节",
    )
    ap.add_argument(
        "--distribution", choices=("internal", "partner", "customer"),
        default="internal", help="报告分发级别（决定报告给谁看）",
    )
    ap.add_argument(
        "--max-impact", choices=("read_only", "reversible_write"),
        default="read_only", help="影响顶棚（默认 read_only；写类 oracle 需显式抬高）",
    )
    ap.add_argument(
        "--deny", default="",
        help="禁止触碰的主机（逗号分隔，通常来自档的 scope.deny）；命中即整任务 BLOCKED",
    )
    ap.add_argument(
        "--qps", type=float, default=1.0,
        help="出网限速（请求/秒）。默认 1.0 与闸门默认一致；平台会按档的 policy.qps 传",
    )
    ap.add_argument(
        "--max-faces", type=int, default=5,
        help="每个 oracle 最多跑几条适用的面（默认 5；0 视为 1）",
    )
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

    # 证据目录只属于一轮（B153）：capsule 是内容寻址的，同一目录跑第二次，
    # 上一轮的几份会和这一轮的混成两条链——`verify-chain` 全红，报告说"2 份"
    # 时盘上却是 4 份。与其事后猜哪份属于哪轮，不如在花掉一轮时间**之前**就拒绝。
    stale = sorted((Path(args.out) / CAPSULE_DIR).glob("*.json"))
    if stale:
        print(
            f"拒绝重跑：{Path(args.out) / CAPSULE_DIR} 里已有 {len(stale)} 份 capsule。\n"
            "  证据是内容寻址的：同一目录跑两次会变成两条链，报告里的份数也对不上盘上。\n"
            # 例子要按**用户实际给的** --out 推（B303）：platform 调进来时目录是
            # `out/<批次>/asset-N`，写死 "out/run-2" 会把人指到一个不存在的地方。
            f"  换个 --out（例如 {_next_out(args.out)}），"
            f"或先把上一轮移走（mv {_show(args.out)} {_next_out(args.out)}）。",
            file=sys.stderr,
        )
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
        causes: set[str] = set()
        for binding in routeset.bindings:
            tags_of = tuple(getattr(binding, "semantic_tags", ()) or ())
            if not tags_of:
                why = "这条面没有语义标签（响应形状推不出类型、也没有参数）"
                causes.add("no_tag")
            else:
                hit = sorted({oid for oid, sel in selectors.items() if sel and sel[0] in tags_of})
                if hit:
                    why = "标签 " + ", ".join(tags_of) + " 的 oracle 全被 --exclude 排除"
                    causes.add("excluded")
                else:
                    why = "标签 " + ", ".join(tags_of) + " 没有对应 oracle"
                    causes.add("no_oracle")
            print(f"  - {str(getattr(binding, 'template', ''))[:70]}：{why}", file=sys.stderr)
        # 三种原因对应三种下一步（B278）：只说"为什么没有"不够，
        # 客户拿着这句话不知道该改契约、换面，还是去掉 --exclude。
        for cause in ("no_tag", "no_oracle", "excluded"):
            if cause in causes:
                print(f"下一步：{_NO_ORACLE_NEXT[cause]}", file=sys.stderr)
        print(f"账本：{out / 'coverage.json'}（缺口 {len(empty['gaps'])} 条）", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    capsules_dir = out / CAPSULE_DIR

    # 名字**不能**叫 primary/secondary：下面 `for primary in faces` 会把它们覆写成
    # RouteBinding，闭包随后 `.strip()` 就崩（本批实测撞到：`oracle_crash:AttributeError
    # <- 'RouteBinding' object has no attribute 'strip'`）。这是晚绑定闭包的经典坑。
    _token_secondary = (args.token_secondary or "").strip()
    if not _token_secondary and args.token_secondary_file:
        try:
            _token_secondary = Path(args.token_secondary_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            print(f"第二身份 token 文件读不了：{exc}", file=sys.stderr)
            return 2
    _token_primary = (args.token or "").strip()
    # 真监听器（B383）：`--oob` 只给了"域名"，没有收回调的地方——于是 `ssrf_oob`
    # 永远拿不到 `oob_seen`，只能判否。`--oob-listen` 起一个回环监听并把**它**注入成 OOB。
    _oob_sink = None
    _dead_host = ""
    if args.oob_listen:
        from veripen_oracles.oob import HttpListener  # noqa: PLC0415

        _oob_sink = HttpListener(endpoint_value="", host=LOOPBACK_HOST).start()
        # ponytail: 监听器随进程退出关闭（main 有多个 return 点，逐处 stop 会把改动摊开
        # 到十几行；进程级清理足够安全。若将来 run 变成常驻服务，改成 finally 或 with。）
        atexit.register(_oob_sink.stop)
        _oob_sink.endpoint_value = f"http://{LOOPBACK_HOST}:{_oob_sink.port}"
        # 对照要一个"不该被连上"的地址。现取一个**刚释放**的环回端口：
        # 绑定 → 读端口 → 立刻关闭。理论上可能被别的进程抢到（race），
        # 但那只会让对照偶发命中，方向是 fail-closed（判不出来，不会假判）。
        _dead = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _dead.bind((LOOPBACK_HOST, 0))
        _dead_port = _dead.getsockname()[1]
        _dead.close()
        _dead_host = f"http://{LOOPBACK_HOST}:{_dead_port}"
        print(f"OOB 监听：{_oob_sink.endpoint_value}（回调记录只在本进程）· 对照：{_dead_host}")
    # `token_for` 是 oracles 区分身份的唯一入口（越权/双身份类按 account 取 token）。
    # 以前 run 只传一个 `--token`，于是 `auth:secondary` 永远满足不了——
    # 而 hunt 早就支持 `--token-secondary`（B378 实测到这条不对称）。
    transport = CoreTransport(
        token_for=(lambda account: _token_secondary if account == "secondary" else _token_primary)
        if (_token_primary or _token_secondary) else None,
        oob=(_oob_sink or (args.oob or None)),
        timeout_s=10.0,
    )
    # 出网闸门：没有它任何请求都抛 GuardMissing（闸门不是可选项）
    from veripen_core.tools import http as http_tool

    # 默认 qps=1 时 burst 仍是 2（与闸门默认一致，不改老行为）；提速时 burst 跟着涨
    guard = build_guard(host, routeset, tuple(oracles),
                        rate=max(0.1, float(args.qps)),
                        burst=max(2.0, float(args.qps)),
                        deny=[x.strip() for x in args.deny.split(",") if x.strip()],
                        ceiling=args.max_impact)
    http_tool.install_guard(guard)
    # 事件库（B339）：给了 --db 就落库；不给就完全不变（不新增副作用）
    db_events = None
    db_task = ""
    if args.db:
        from veripen_core.blackboard import EventLog
        from veripen_core.store import Store

        db_task = f"run-{int(time.time())}"
        db_events = EventLog(Store(str(args.db)).init())
    started = time.time()
    # OOB 符号（B378）：oracle 的模板里写 `{{ref:OOB}}`，不给就 SymbolMissing。
    # hunt 早就这么做了；run 之前一个符号都不注入。
    symbols_map: dict[str, str] = {}
    if _oob_sink is not None:
        symbols_map["OOB"] = _oob_sink.endpoint_value
        symbols_map["OOB_URL"] = _oob_sink.endpoint_value
        if _dead_host:
            symbols_map.setdefault("DEAD_HOST", _dead_host)
    if args.oob:
        symbols_map["OOB"] = str(args.oob)
        symbols_map["OOB_URL"] = "https" + ":" + "//" + str(args.oob)
    raw_symbols = args.symbols or (
        Path(args.symbols_file).read_text(encoding="utf-8") if args.symbols_file else "")
    if raw_symbols.strip():
        try:
            parsed = json.loads(raw_symbols)
        except json.JSONDecodeError as exc:
            print(f"符号表不是合法 JSON：{exc}", file=sys.stderr)
            return 2
        if not isinstance(parsed, dict):
            print("符号表必须是一个 JSON 对象", file=sys.stderr)
            return 2
        symbols_map.update({str(k): str(v) for k, v in parsed.items()})
    # 身份符号跟凭据一起自动补（B379）：oracle 用 `account="STRANGER"` 表达"换个人发"，
    # 值只需是身份名（bench 与 demo 里都是 `secondary`），不是 token 本身。
    if _token_primary:
        symbols_map.setdefault("OWNER", "primary")
    if _token_secondary:
        symbols_map.setdefault("STRANGER", "secondary")
    run_symbols = SymbolTable(symbols_map) if symbols_map else SymbolTable()
    # 按 oracle 传参（B386）：`mtls_required` 需要操作员声明「这里应该是双向认证」，
    # 否则它只能保持静默——能力有了、开关没有，等于没有（B378 的教训）。
    oracle_params: dict[str, dict[str, object]] = {}
    if str(getattr(args, "oracle_params", "") or "").strip():
        try:
            parsed_params = json.loads(args.oracle_params)
        except json.JSONDecodeError as exc:
            print(f"--oracle-params 不是合法 JSON：{exc}", file=sys.stderr)
            return 2
        if not isinstance(parsed_params, dict):
            print("--oracle-params 必须是 {oracle_id: {参数}} 形状的对象", file=sys.stderr)
            return 2
        oracle_params = {str(k): dict(v) for k, v in parsed_params.items() if isinstance(v, dict)}
    results: list[tuple[str, Any, Any, Any]] = []   # (oracle_id, meta, result, binding)
    render_state: dict[str, Any] = {"thread": None, "sent": []}
    face_runs: list[Any] = []   # list[FaceRun]：账本观测的唯一来源
    try:
        for oracle_id in oracles:
            cls = next((c for c in ALL_ORACLES if c.meta.id == oracle_id), None)
            if cls is None:
                continue
            try:
                # mypy：`BaseOracle.__init__` 的签名是 (params: BaseModel|None, **values)，
                # 而这里传的是 JSON 解出来的散值——pydantic 会在 params_model 上校验，
                # 类型层面只能显式忽略（错的值会被下面的 except 抓住并明说）。
                oracle = cls(**oracle_params.get(oracle_id, {}))  # type: ignore[arg-type]
            except Exception as exc:  # noqa: BLE001 - 参数对不上要明说，不要静默忽略
                print(f"  {oracle_id:<24} 跳过：--oracle-params 不匹配（{exc}）", file=sys.stderr)
                continue
            tags = [str(t) for t in getattr(oracle.meta, "selector_tags", ())]
            # **每一条**适用的面都要跑，不只第一条：否则发现出来的面只判一条，
            # 其余永远记 face_without_oracle——覆盖率缺口有一半是这么来的。
            faces = [
                b for b in routeset.bindings
                if tags and tags[0] in getattr(b, "semantic_tags", ())
            ][: max(1, args.max_faces)]
            if not faces:
                print(f"  {oracle_id:<24} 跳过：路由未解析")
                continue
            for primary in faces:
                bindings: dict[str, Any] = {
                    tag: primary for tag in tags
                    if tag in getattr(primary, "semantic_tags", ())
                }
                try:
                    # 闸门按 oracle 放行：不设这一项，请求会被 plan_denied 拦下
                    transport.oracle_id = oracle_id
                    # 需要渲染的 oracle 换传输：HTTP 看不到"脚本有没有真跑"
                    active: Any
                    if bool(getattr(cls, "needs_render", False)):
                        if render_state.get("thread") is None:
                            from veripen_core.render.worker import RenderThread

                            render_state["thread"] = RenderThread()
                        from veripen_oracles.render_transport import RenderTransport

                        active = RenderTransport(
                            render_state["thread"], oracle_id=oracle_id
                        )
                    else:
                        active = transport
                    result = run_oracle(
                        oracle, primary, active,
                        symbols=run_symbols, nonce=f"run-{oracle_id}",
                        oob=(_oob_sink or (args.oob or None)),
                        secondary_token=(_token_secondary or None),
                        token=args.token or None, bindings=bindings,
                        # 严格模式（B173）：缺 requires 就 BLOCKED。`run` 没有 OOB 通道、
                        # 也没有审批句柄，所以这两类在严格模式下会如实 BLOCKED。
                        strict_requires=args.strict_requires,
                        # 多步判据要按模板挑端点（登录→登出→会话是三个不同端点）
                        templates={b.template: b for b in routeset.bindings},
                    )
                    # 渲染请求也要计入请求数：报告里的 requests 是"真的发出去多少"
                    render_state["sent"].extend(getattr(active, "sent", []))
                except Exception as exc:  # noqa: BLE001 —— 目标上什么都可能发生，如实记
                    # 缺输入（符号没注入）是**明确的 BLOCKED**，不是"没跑"：
                    # 记成 not_run 会让账本看起来像我们跳过了它（D1）。
                    from veripen_core.models import Verdict as _V
                    from veripen_oracles.common.harness import RunResult as _Run
                    from veripen_oracles.common.harness import SymbolMissing as _Missing

                    # 传输层失败（连不上/超时）不是"缺输入"，也不该把 Windows 异常串
                    # 当原因码（B299）：给它一个稳定码，报告里才读得出该去查网络。
                    from veripen_core.guard.errors import ScopeViolation as _Scope

                    detail = ""
                    if isinstance(exc, _Scope):
                        # 被边界挡下是**明确拒绝**，不是"判不了"（B311）：
                        # 原因码用稳定值（peer_in_deny / peer_not_in_scope），
                        # 不把异常 repr 与 dict 塞进报告。
                        reason = getattr(exc, "reason", "") or "scope_violation"
                    elif isinstance(exc, _Missing):
                        reason = f"symbol_missing:{exc}"
                        # 原因码保持稳定（机器读），"怎么补"只进人话输出（B406）
                        from veripen_oracles.common.harness import symbol_hint as _hint

                        detail = f"提供方式：{_hint(str(exc))}"
                    elif isinstance(exc, (ConnectionError, TimeoutError, OSError)):
                        kind = {
                            "ConnectionRefusedError": "connection_refused",
                            "ConnectionResetError": "connection_reset",
                            "TimeoutError": "timeout",
                        }.get(type(exc).__name__, type(exc).__name__.lower())
                        reason = f"transport_error:{kind}"
                    else:
                        # 与 B299/B311 同一条纪律：原因码必须**稳定**、与 locale 无关
                        # （B364）。异常原文仍在下一行打印，报告里只放码——
                        # 否则交付物里会出现 `RuntimeError: 渲染线程启动失败…` 这种
                        # 既非码也非人话、换台机器就变样的一行。
                        detail = " ".join(str(exc).split())[:80]
                        reason = f"oracle_crash:{type(exc).__name__}"
                    print(f"  {oracle_id:<24} {'BLOCKED':<14} {reason[:60]}"
                          + (f" <- {detail}" if detail else ""))
                    blocked = _Run(_V.BLOCKED, reason, None, (), {})
                    results.append((oracle_id, oracle.meta, blocked, primary))
                    if db_events is not None:
                        db_events.record(task_id=db_task, kind="oracle", ok=False,
                                         oracle_id=oracle_id, err_class="blocked",
                                         detail={"verdict": "BLOCKED", "reason": reason[:80]})
                    from veripen_core.coverage import FaceRun as _Face

                    face_runs.append(_Face(
                        oracle_id=oracle_id,
                        route_ref=str(getattr(primary, "ref", "")),
                        template=str(getattr(primary, "template", "")),
                        verdict="BLOCKED",
                        reason=reason,
                        face_index=faces.index(primary) + 1,
                        faces_total=len(faces),
                    ))
                    continue
                verdict = str(getattr(result.verdict, "value", result.verdict))
                face = str(getattr(primary, "template", ""))[:44]
                print(
                    f"  {oracle_id:<24} {verdict:<14} "
                    f"{str(getattr(result, 'reason', ''))[:36]:<36} {face}"
                )
                results.append((oracle_id, oracle.meta, result, primary))
                if db_events is not None:
                    # `ok` 的含义是**执行成功**，不是"有洞"（B341）：
                    # REFUTED 是"测了、不成立"，它跑得好好的，不该进异常账。
                    db_events.record(
                        task_id=db_task, kind="oracle",
                        ok=verdict in ("VERIFIED", "REFUTED"),
                        oracle_id=oracle_id, route_ref=str(getattr(primary, "ref", "")),
                        err_class=None if verdict in ("VERIFIED", "REFUTED") else "blocked",
                        detail={"verdict": verdict, "reason": str(getattr(result, "reason", ""))[:80]},
                    )
                from veripen_core.coverage import FaceRun

                face_runs.append(FaceRun(
                    oracle_id=oracle_id,
                    route_ref=str(getattr(primary, "ref", "")),
                    template=str(getattr(primary, "template", "")),
                    verdict=verdict,
                    reason=str(getattr(result, "reason", "") or ""),
                    face_index=faces.index(primary) + 1,
                    faces_total=len(faces),
                ))


    finally:
        http_tool.uninstall_guard()
        thread = render_state.get("thread")
        if thread is not None:
            try:
                thread.close()
            except Exception:  # noqa: BLE001 - 关不掉不该盖住主流程
                pass
    if not results:
        print("全部 oracle 未产出结果", file=sys.stderr)
        return 1

    # ── 证据封存：只为 VERIFIED 造 capsule，按报告顺序串链 ──────────
    from veripen.bench.capsule import build_capsule

    docs: list[CapsuleDoc] = []
    #: 键是 `(oracle, 面)`：只按 oracle 记会让后一个面覆盖前一个，
    #: 结果是"面 A 的发现引用面 B 的证据"（B148）。
    index_of: dict[tuple[str, str], CapsuleDoc] = {}
    for oracle_id, meta, result, binding in results:
        if str(getattr(result.verdict, "value", result.verdict)) != "VERIFIED":
            continue
        face_ref = str(getattr(binding, "ref", ""))
        try:
            doc = build_capsule(
                case_id=f"run-{oracle_id}-{_ref_tail(binding)}",
                oracle_id=oracle_id,
                oracle_version=str(_meta(meta, "version", "1")),
                verdict=result.verdict,
                judgement=result.judgement,
                pairs=result.pairs,
                baseline_params=result.baseline_params,
                routeset=routeset,
                route_ref=face_ref,
                binding=binding,
                cwe=str(_meta(meta, "cwe", "")),
                created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
        except Exception as exc:  # noqa: BLE001 —— 证据不全就明说，不造假的
            print(f"  证据构造失败 {oracle_id}：{type(exc).__name__}: {str(exc)[:60]}")
            continue
        docs.append(doc)
        index_of[(oracle_id, face_ref)] = doc

    sealed = chain_capsules(docs) if docs else []
    for cap in sealed:
        save_capsule(cap, capsules_dir)
        # 用**封印后 capsule 自己的** `route.ref` 回填：它记录的就是这一面的路由
        cap_ref = str(getattr(getattr(cap, "route", None), "ref", ""))
        index_of[(cap.oracle.id, cap_ref)] = cap

    # ── 报告 ────────────────────────────────────────────────────
    from veripen.report.lint import build_manifest
    from veripen.report.render import ReportInput, render_json, render_markdown, render_sarif

    views = [
        _findings_view(
            asset_id=host, oracle_id=oid, meta=meta, result=res,
            binding=binding,
            capsule=index_of.get((oid, str(getattr(binding, "ref", "")))),
            observes_failures=bool(getattr(
                next((c for c in ALL_ORACLES if c.meta.id == oid), None),
                "observes_failures", False)),
        )
        for oid, meta, res, binding in results
    ]
    # ── 覆盖账本（B87）：先算账，再写报告——报告里要带缺口清单 ──────
    from veripen_core.coverage import Observation, build_coverage, write_coverage

    oracle_tags = {
        str(c.meta.id): tuple(str(t) for t in getattr(c.meta, "selector_tags", ()))
        for c in ALL_ORACLES
    }
    # 观测只从 face_runs 出：别在调用方再拼一遍（B122）
    from veripen_core.coverage import observations_from

    observations = observations_from(face_runs)
    coverage = build_coverage(
        asset_id=host,
        bindings=routeset.bindings,
        oracle_tags=oracle_tags,
        selected=oracles,
        observations=observations,
        excluded=sorted(excluded),
    )

    # 能力边界（B396/B397）：算一次，三条路径共用一份实现
    from veripen.report.limits import capability_limits  # noqa: PLC0415

    _limits = capability_limits()
    report = ReportInput(
        engagement=host,
        excluded=sorted(excluded),
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
        capability_limits=_limits,
        render_time=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        # 报告里的复现命令要指到**这一轮**的证据目录。
        # 用**相对**形式（B189）：绝对路径会把操作员的机器布局写进交付物，
        # 换台机器/换个目录就读不出来——报告和证据本来就是一起发出去的。
        capsule_dir=CAPSULE_DIR,
        frameworks=[x.strip() for x in args.frameworks.split(",") if x.strip()],
        distribution=args.distribution,
    )

    # 三视图 + 清单（B254）：以前 `veripen run` 只写 md 与 json，SARIF 与
    # `report.manifest.json` 只在演示脚本里有——于是"产品路径产出的报告"进不了客户的
    # CI（SARIF 是扫描平台读的），也没法证明"报告没被手改"（manifest 是那件事的凭据）。
    # 演示里怎么写，产品里就怎么写：同一份 dict 进三份产物。
    md_text = render_markdown(report)
    audit_text = ""
    if db_events is not None and db_task:
        from veripen.report.audit import AuditInput, render_audit_section

        # `for_task` 给的是 Event 对象，审计渲染吃 dict（与平台那一路同口径）
        audit_events = [
            {"seq": e.seq, "kind": e.kind, "ok": e.ok, "tool": e.tool,
             "oracle_id": e.oracle_id, "route_ref": e.route_ref,
             "elapsed_ms": e.elapsed_ms, "detail": dict(e.detail or {})}
            for e in db_events.for_task(db_task)
        ]
        # 预算账要有真数（B340）：只传 events 时五行全是"未提供"，
        # 读者看不出这一轮发了多少请求、跑了多久。
        audit_text = render_audit_section(AuditInput(
            task_id=db_task,
            events=audit_events,
            budget={
                "requests": len(render_state["sent"]),
                "elapsed_s": round(time.time() - started, 1),
                "steps": len(results),
                # run 不调模型：tokens **确实是 0**（B341）——"未提供"会被读成没统计
                "tokens": 0,
            },
        ))
        if audit_text.strip():
            from veripen.report.render import render_markdown_with_audit

            md_text = render_markdown_with_audit(report, audit_text)
    js_payload = render_json(report)
    sarif = render_sarif(report)
    from veripen.report.lint import dumps_artifact

    # 落盘必须与算哈希用同一份字符串，且换行固定 LF（B316）：
    # 否则 Windows 上的 CRLF 翻译会让 `sha256sum` 与 manifest 对不上。
    (out / "findings.json").write_text(dumps_artifact(js_payload), encoding="utf-8", newline=chr(10))
    (out / "report.md").write_text(md_text, encoding="utf-8", newline=chr(10))
    (out / "report.sarif").write_text(dumps_artifact(sarif), encoding="utf-8", newline=chr(10))

    # 越界事件落盘（B332）：以前只活在 ScopeGuard.events 里，进程一退就没了。
    violations = guard.scope.violations()
    bounds_text = json.dumps({"count": len(violations), "events": violations},
                             ensure_ascii=False, indent=2)
    (out / "bounds.json").write_text(bounds_text, encoding="utf-8", newline=chr(10))

    # 清单最后写，并把 bounds.json 也纳进来（B333）：它是审计证据，被改也要能发现
    import hashlib as _hashlib

    manifest = build_manifest(
        markdown=md_text, sarif=sarif, js=js_payload,
        extra_files={"bounds.json": _hashlib.sha256(bounds_text.encode("utf-8")).hexdigest()},
    )
    (out / "report.manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

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
    if db_events is not None:
        print(f"审计：{len(db_events.for_task(db_task))} 条事件 → {args.db}（报告里附审计五表）")
    print(f"交付：{(out / 'report.md').name} / {(out / 'findings.json').name}"
          f" / {(out / 'report.sarif').name} / {(out / 'report.manifest.json').name}")
    print(f"耗时：{time.time() - started:.1f}s")
    bad = [c for c in sealed if not verify_self_sha(c)]
    if bad:
        print(f"证据自校验失败 {len(bad)} 份", file=sys.stderr)
        return 1
    # 一条都没验证成功、而且有执行失败 → 这次运行不能算"跑完了"（B300）。
    # 实测：目标整台不可达时 exit=0、报告写着 `VERIFIED 0 / 共 3 项`，CI 会当成绿。
    errors = sum(1 for v in views if v.outcome.value == "error")
    # 「一项都没跑成」的判据（B300/B353/B358）：**没得出结论 且 有东西没跑成**。
    # REFUTED 也算结论（测了、不成立），但它和"被挡下"共用 not_run 这一格
    # （09 §4 只有四态），所以必须靠 `refuted` 标志把两者拆开：
    #   - 安全靶场全 REFUTED  → 有结论 → 0
    #   - 全被黑名单挡下       → 没结论、有 blocked → 1
    #   - 目标不可达           → 没结论、有 error   → 1
    refuted = sum(1 for v in views if bool(getattr(v, "refuted", False)))
    blocked_count = sum(
        1 for v in views
        if v.outcome.value == "not_run" and not bool(getattr(v, "refuted", False))
    )
    # `refuted` 不进这个判据：全 REFUTED 时 blocked=0、errors=0，自然落到 0；
    # 而"有 error 或 有被挡下"本身就是"不能当覆盖"的理由，不该被别的结论抵消。
    if verified == 0 and (errors or blocked_count):
        why = "、".join(
            x for x in (f"{errors} 项执行失败" if errors else "",
                        f"{blocked_count} 项被挡下未执行" if blocked_count else "") if x)
        print(f"没有任何一项得出结论（{why}）——这次运行不能当作覆盖", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
