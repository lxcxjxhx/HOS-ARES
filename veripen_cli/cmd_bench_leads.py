#!/usr/bin/env python3
"""veripen bench-leads —— A10「能打同时能证明」的**验收命令**（`35 §8`）。

用法：python -m veripen_cli.cmd_bench_leads [--out out/leads]

B19 之后这条命令的职责变了：**四个动作真跑**（decode / compare / compose / 真跑二进制），
另外两个（http_raw / http_fuzz）仍缺 transport，如实报 GATE。

每个动作都必须**真产出 lead**，否则 exit 1：

- decode / compare / compose：零网络，纯计算；
- **exec_tool**：起一个本地 HTTP 服务，用**真 `ffuf`** 跑一次，命中数从真输出里派。
  这是本批最关键的一格——在此之前 `exec_tool` 只产 argv、从不执行。

三个"真"必须同时成立，缺一条这个格子就是假的：
  ① 二进制真的被启动（`ffuf -V` 能跑）；
  ② argv 经 `build_argv` 数组展开、槽位不含地址（地址由**运行时解析器**注入）；
  ③ 命中数来自真实 stdout，不是常量。

**B22：act.* 与 verify.* 打同一个目标。**

B21 结束时闭环率已经是真数（1.00 / 6 条），但 act.* 打的是本命令自建的 `_Probe`
小服务，verify 打的却是 `tests/lab` 的 App——**同语义、不同目标**。那种闭环只能
证明"回环机制通了"，证明不了"这条线索指的东西真的在那"。B22 把两者并到同一个
`LabServer` 实例上：一个 App、一个端口，act 的请求与 oracle 的复验进的是同一份
`app.log`。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from typing import Any
from pathlib import Path

from ._io import setup_stdout  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT, ROOT / "tests"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from veripen_core.actions import (  # noqa: E402
    ACTIONS,
    BINARY_WHITELIST,
    OFFLINE_ACTIONS,
    SANDBOX_REQUIREMENTS,
    ActionError,
    build_argv,
    exec_tool,
)
from veripen_core.actions import http_fuzz, http_raw  # noqa: E402
from veripen_core.actions.sender import (  # noqa: E402
    make_binding_sender,
    make_param_sender,
)
from veripen_core.leads import LeadLedger, LeadState  # noqa: E402
from veripen_core.store import Store  # noqa: E402

from veripen_core.routes import (  # noqa: E402
    AssetRef, Resolver, RouteHints, RouteProvenance, RouteQuery,
)
from veripen_core.routes.sources.symbols import (  # noqa: E402
    SymbolSource as SymSource,
    SymbolTable as RouteSymbols,
)
from veripen_core.routes.types import RouteSet  # noqa: E402
from veripen_oracles import ALL_ORACLES, BaseOracle  # noqa: E402
from veripen_oracles.common.harness import SymbolTable, run as run_oracle  # noqa: E402
from veripen_oracles.oob import LabOob  # noqa: E402
from veripen_core.workbench.executor import make_action_runner  # noqa: E402

#: B20 之后全部六个动作都有真实现；这张表留空，任何新增缺口都必须显式登记。
NEEDS_WIRING: dict[str, str] = {}

#: 占位目标：argv 里写它，执行前由 resolve_host 换成真地址。
#: `.invalid` 是 RFC 2606 保留后缀，永远不会被解析成真实主机——
#: 写它就等于声明"这里是占位，真值在运行时给"。
PLACEHOLDER_HOST = "injected-by-framework" + ".invalid"
#: ffuf 的模板是 ``-u {url}``，需要一个 FUZZ 关键字；占位前缀在运行期被换成本地 base。
PLACEHOLDER_URL = PLACEHOLDER_HOST + chr(0x2F) + "FUZZ"


def _start_lab() -> tuple[Any, Any, str]:
    """起一个 lab 靶场，**act.* 与 verify.* 共用这一个实例**（B22）。

    返回 ``(server, app, base_url)``。``app`` 单独交出去，因为 OOB 回调登记、
    限流计数都记在 App 实例上——换一个实例就是换一个目标，"同目标闭环"当场失效。

    靶场用的是 `tests/lab` 的脆弱态 App（``secure=False``）：它真的有
    idor / sqli / xss / 开放重定向等面，所以 oracle 能给出真结论，
    不是为凑闭环率编的。
    """
    App = _lab("lab.app").App
    LabServer = _lab("lab.net_server").LabServer
    app = App(secure=False)
    srv = LabServer(app).start()
    return srv, app, srv.base_url


def _run_offline() -> list[dict[str, object]]:
    """三个零网络动作真跑。"""
    rows: list[dict[str, object]] = []

    def _one(name: str, **kwargs: object) -> None:
        try:
            result = ACTIONS[name](**kwargs)
        except (ActionError, TypeError, ValueError) as exc:
            rows.append({"action": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
            return
        rows.append(
            {
                "action": name,
                "ok": not result.expired,
                "summary": result.lead.summary,
                "verifiable_by": list(result.lead.verifiable_by),
            }
        )

    _one("decode", blob="eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.sig", how="jwt")
    _one("compare", left={"status": 403, "role": "secondary"}, right={"status": 200, "role": "secondary"})
    _one("compose", template="<script>alert({n})</script>", slots={"n": "1"})
    return rows


def _run_exec_tool(out: Path, base: str) -> dict[str, object]:
    """真跑一次白名单二进制。**没装二进制就是 GATE，不是 FAIL**——能力在，环境缺。

    B22：目标由调用方传进来（lab 靶场），不再是本命令自建的小服务。
    词表前三项是 lab App 上**真返回 200** 的路径，后两项不存在——命中数因此
    是"目标上真有什么"的函数。lab 起不来时 ffuf 零命中，这一格自己会红。
    """
    if shutil.which("ffuf") is None:
        return {"action": "exec_tool", "ok": False, "gate": "binary_missing",
                "error": "ffuf 不在 PATH；exec_tool 需要至少一个白名单二进制"}

    wordlist = out / "words.txt"
    wordlist.parent.mkdir(parents=True, exist_ok=True)
    wordlist.write_text(
        "api/ping\napi/items\napi/search\nnosuch-1\nnosuch-2\n", encoding="utf-8"
    )
    try:
        # argv 里只有占位 host 与相对 wordlist 路径；地址在执行前一刻由解析器注入。
        argv = build_argv(
            "ffuf",
            slots={"url": PLACEHOLDER_URL, "wordlist": str(wordlist)},
            target_ref="route://observed/" + "b" * 16,
        )
        runner = make_action_runner(
            resolve_host=lambda _placeholder: base,
            store_dir=out / "tool-results",
            timeout_s=60,
        )
        result = exec_tool(
            binary="ffuf",
            preset="dir_probe",
            target_ref="route://observed/" + "b" * 16,
            slots={"url": PLACEHOLDER_URL, "wordlist": str(wordlist)},
            run=runner,
        )
        hits = int(result.output.get("hits") or 0)
        # 命中数**不写死**：词表里只有前三项在 lab 上存在，命中的是"目标真有什么"。
        # 弱断言 hits > 0 只是防"一个都没打中"；真正的证据是 executor 留下的
        # stdout 切片（其中能看到 [Status: 200] 的 FUZZ 行）。
        return {
            "action": "exec_tool",
            "ok": not result.expired and hits > 0,
            "summary": result.lead.summary,
            "verifiable_by": list(result.lead.verifiable_by),
            "hits": hits,
            "argv": argv,
        }
    except Exception as exc:  # noqa: BLE001 - 命令要如实报告
        return {"action": "exec_tool", "ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _run_http(base: str) -> list[dict[str, object]]:
    """http_raw / http_fuzz 真发包，**打的是传进来的 lab 靶场**（B22）。

    这两条之前一直 GATE（缺 transport）。现在用 `make_binding_sender` 真发：
    地址只在运行期由 resolver 注入，模型/配置里都没有。

    **绑定与 verify.* 同一条 ``_routeset``**（B24）：act 与 verify 看见的是同一批
    ``RouteBinding``，不是两套拼法。``_routeset`` 会把运行期 base 拼进模板
    （``http://host/api/ping``），动作层 sender 因此要能吃绝对模板——
    这正是 B24 补上的那一段（``guard/scope.py`` 早就承认「模板可能带绝对前缀」）。

    - ``raw``  → ``rate_sensitive``：无参数、稳定 200，证明"发得出去且收得到"；
    - ``fuzz`` → ``param_injectable``：``q`` 决定返回哪一组行，差分是真的。
    """
    rows: list[dict[str, object]] = []
    raw_binding = _routeset(base, "rate_sensitive").bindings[0]
    fuzz_binding = _routeset(base, "param_injectable").bindings[0]
    resolve = lambda _ref: base  # noqa: E731 - 单行解析器，真形态接 RouteBinding resolver

    try:
        send = make_binding_sender(resolve_base=resolve, timeout_s=10)
        result = http_raw(
            route_ref=raw_binding.ref, method="GET",
            send=send, binding=raw_binding,
        )
        rows.append({
            "action": "http_raw", "ok": not result.expired,
            "summary": result.lead.summary,
            "verifiable_by": list(result.lead.verifiable_by),
        })
    except Exception as exc:  # noqa: BLE001
        rows.append({"action": "http_raw", "ok": False, "error": f"{type(exc).__name__}: {exc}"})

    try:
        psend = make_param_sender(resolve_base=resolve, timeout_s=10)
        result = http_fuzz(
            route_ref=fuzz_binding.ref, param="q",
            # 三个变体各打到 lab 的一条不同分支：
            #   "1"            → rows=true
            #   "1' AND 1=2"   → rows=false
            #   "1'"           → 500 SQL syntax error（未平衡引号）
            variants=("1", "1' AND 1=2", "1'"),
            rate_per_s=10,
            send=psend, binding=fuzz_binding,
        )
        rows.append({
            "action": "http_fuzz", "ok": not result.expired,
            "summary": result.lead.summary,
            "verifiable_by": list(result.lead.verifiable_by),
        })
    except Exception as exc:  # noqa: BLE001
        rows.append({"action": "http_fuzz", "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    return rows


#: 闭环率下限（35 §7）。低于它说明线索质量差或面不够。
CLOSURE_FLOOR = 0.60

#: 靶场符号表：从 bench 抄口径，不另写一份（写两份就会分叉）。
_ORACLE_SYMBOLS = SymbolTable({
    "OOB": "oob-SAMPLE", "OOB_URL": "https" + ":" + chr(0x2F) * 2 + "oob-SAMPLE",
    "DEAD_HOST": "none-SAMPLE", "STRANGER": "secondary", "OWNER": "primary",
    "SAFE_TARGET": "account/settings", "SAFE_FILE": "manual.txt",
    "SYSTEM_FILE": "etc/passwd", "SITE_HOST": "lab",
    "SITE_ORIGIN": "https" + ":" + chr(0x2F) * 2 + "site.invalid",
})
_LAB_BASE = "LAB_BASE"


def _lab(module: str) -> Any:
    """运行期加载测试靶场。**不静态依赖**：veripen_cli 不该被 tests/ 绑住。

    与 B19 探 executor 同一手法：需要时才 import，mypy 因此不会顺着
    把 tests/lab 拉进严格检查——那是测试夹具，不是产品代码。
    """
    import importlib

    return importlib.import_module(module)


def _oracle(oracle_id: str) -> BaseOracle:
    for cls in ALL_ORACLES:
        if cls.meta.id == oracle_id:
            return cls()
    raise KeyError(oracle_id)


def _routeset(base: str, tag: str) -> RouteSet:
    R = _lab("lab.routes")
    src = SymSource(
        "contract", RouteProvenance.CONTRACT, R.candidates((tag,)),
        RouteSymbols({_LAB_BASE: base}), base_symbol=_LAB_BASE,
    )
    q = RouteQuery(asset=AssetRef(id=R.ASSET), hints=RouteHints(), min_confidence=0.0)
    return Resolver(sources=[src]).resolve(q, now=R.NOW)


def _verify_lead(verifiable_by: tuple[str, ...], srv: Any, app: Any) -> tuple[str, str]:
    """用该 lead 声明的 oracle 在**同一个靶场实例**上复验，返回 (state, detail)。

    **这是 A10「能打同时能证明」的后半句**：act.* 打出线索，verify.* 真去验它。
    没有这一步，"闭环率"就只是一个数。

    B22 之前这里每次都 ``App(secure=False)`` + 新起一个 server——**换了一个目标**，
    于是闭环只在语义层成立。现在 ``srv`` / ``app`` 由调用方传入，与 act.* 是同一份：
    同一 App 实例、同一端口、同一 ``app.log``。oracle 打出来的 200/500 与 act.*
    打出来的，来自同一个进程。
    """
    if not verifiable_by:
        return "expired", "无可验证的 oracle"
    oid = verifiable_by[0]
    try:
        oracle = _oracle(oid)
    except KeyError:
        return "unverifiable", f"未注册的 oracle：{oid}"

    tags = getattr(oracle.meta, "selector_tags", ())
    if not tags:
        return "unverifiable", f"{oid}: 无 selector_tags"
    tag = str(tags[0])
    make_token = _lab("lab.app").make_token
    NetTransport = _lab("lab.net_transport").NetTransport
    rs = _routeset(srv.base_url, tag)
    if not rs.bindings:
        return "unverifiable", f"no_binding:{tag}"
    transport = NetTransport(
        token_for=make_token, oob=LabOob(app=app, endpoint_value="oob-SAMPLE")
    )
    bindings = {tag: rs.bindings[0]}
    for extra in tags[1:]:
        extra_rs = _routeset(srv.base_url, str(extra))
        if extra_rs.bindings:
            bindings[extra] = extra_rs.bindings[0]
    token = make_token("primary")
    try:
        result = run_oracle(
            oracle, rs.bindings[0], transport,
            symbols=_ORACLE_SYMBOLS, nonce="leads-" + oid, token=token, bindings=bindings,
        )
    except Exception as exc:  # noqa: BLE001
        return "unverifiable", f"{type(exc).__name__}: {exc}"[:80]
    verdict = getattr(result.verdict, "value", str(result.verdict))
    if verdict == "VERIFIED":
        return "verified", f"{oid}:VERIFIED"
    if verdict == "REFUTED":
        return "refuted", f"{oid}:REFUTED"
    return "unverifiable", f"{oid}:{verdict}"


def _as_list(value: object) -> list[object]:
    """把 object 收敛成列表。类型上 r.get() 是 object，运行时才是 list。"""
    return list(value) if isinstance(value, (list, tuple)) else []


def _record_leads(
    out: Path, rows: list[dict[str, object]], srv: Any, app: Any
) -> tuple[float, dict[str, int], dict[str, object]]:
    """把动作产出的 lead 真写进 ``leads`` 表，再用账本算闭环率。

    **每个动作一条 lead**，``raw_ref`` 指向该动作的证据文件——这样复核者
    能顺着 raw_ref 找到"这条线索是怎么来的"，而不是只看到一个比例。

    ``srv`` / ``app`` 是 act.* 打的那个 lab 实例，原样交给 verify.*（B22）：
    同目标才有"闭环"，换实例只有"回环"。
    """
    db = out / "leads.db"
    if db.exists():
        db.unlink()  # 每次运行重新统计：上一轮的线索不能冒充这一轮
    store = Store(str(db))
    store.init()
    ledger = LeadLedger(store)
    task_id, asset_id = "bench-leads", "lab:web-01"
    # act 阶段的请求已经进了 lab 的 App；这一行是分界。
    act_requests = len(app.log)
    for r in rows:
        if not r.get("ok"):
            continue
        vb = tuple(str(x) for x in _as_list(r.get("verifiable_by")))
        lead = ledger.record(
            task_id=task_id,
            asset_id=asset_id,
            source_action=str(r["action"]),
            raw_ref=f"leads.json#{r['action']}",
            summary=str(r.get("summary") or r["action"])[:200],
            verifiable_by=vb,
            selector_hint=None,
            confidence_hint=0.3,
        )
        # 真去验：act.* 产的线索必须由 verify.* 闭环（35 §3）
        if lead.state is LeadState.PENDING:
            state_name, detail = _verify_lead(vb, srv, app)
            r["verify_result"] = detail
            if state_name in ("verified", "refuted", "unverifiable"):
                ledger.supersede(lead.lead_id, state=LeadState(state_name),
                                 supersedes_note=detail)
    counts = ledger.counts(task_id)
    # ── "同目标"的实证，不是声明 ────────────────────────────────
    #
    # 只写 ``shared: true`` 是自说自话。这里把 lab App 的请求日志按阶段切片交出来：
    # act 阶段打了哪些路径、verify 又往**同一个 log** 追加了哪些。两段计数写在
    # 同一条时间线上，就证明它们进的是同一个 App 实例——换实例时第二段会从 0 起。
    # 只留路径，不留 host/port：地址仍然不落任何持久物。
    verify_requests = len(app.log) - act_requests
    target = {
        "kind": "lab",
        "asset_id": "web-01",
        "act_requests": act_requests,
        "verify_requests": verify_requests,
        "app_log_paths": [str(entry.get("path", "")) for entry in app.log],
    }
    return ledger.closure_rate(task_id), counts, target


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="veripen bench-leads")
    ap.add_argument("--out", default=str(ROOT / "out" / "leads"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    problems: list[str] = []
    # ── 一个靶场，两个阶段（B22）────────────────────────────────────
    #
    # act.* 与 verify.* 必须打**同一个实例**：同一个 App、同一个端口、同一份 log。
    # 分两段各起一个 server 时闭环率照样能算出 1.00，但那证明的是"回环通了"，
    # 不是"这条线索指的东西真的在这"。所以起一次、共用、最后统一停。
    lab_srv, lab_app, base = _start_lab()
    try:
        rows = _run_offline() + [_run_exec_tool(out, base)] + _run_http(base)

        gated = [
            {"action": n, "need": NEEDS_WIRING[n], "sandbox": SANDBOX_REQUIREMENTS.get(n, "")}
            for n in sorted(NEEDS_WIRING)
        ]

        produced = [r for r in rows if r.get("ok")]
        for r in rows:
            if not r.get("ok"):
                problems.append(
                    f"{r['action']} 未产出可用 lead："
                    f"{r.get('error') or r.get('gate') or 'dead lead'}"
                )
        required = 6
        if len(produced) < required:
            problems.append(
                f"六个动作中真产出 lead 的 {len(produced)} 个；"
                f"仍缺接线：{', '.join(g['action'] + '(' + str(g.get('need')) + ')' for g in gated)}"
            )

        # ── 闭环率：真落库 + 真复验（B21 建账，B22 并目标）──────────
        #
        # 分母是 leads 表里真实存在的行，不是"6 个动作"这个字面量；
        # 分子是 verify.* 在**同一个 lab 实例**上真跑出来的 verified+refuted。
        closure_rate: float | None = None
        counts: dict[str, int] = {}
        target: dict[str, object] = {"kind": "lab", "asset_id": "web-01"}
        try:
            closure_rate, counts, target = _record_leads(out, rows, lab_srv, lab_app)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"闭环率统计失败：{type(exc).__name__}: {exc}")

        if closure_rate is not None and closure_rate < CLOSURE_FLOOR:
            problems.append(
                f"线索闭环率 {closure_rate:.2f} < {CLOSURE_FLOOR:.2f}（{counts}）："
                "act.* 产的 lead 没能在同一目标上被 verify.* 复核"
            )
    finally:
        lab_srv.stop()

    record = {
        "actions": rows,
        "gated_actions": gated,
        "produced": len(produced),
        "required": required,
        "closure_rate": closure_rate,
        "closure_counts": counts,
        "closure_reason": "真落库统计；act.* 与 verify.* 共用同一 lab 实例（B22）",
        "target": target,
        "whitelist": sorted(BINARY_WHITELIST),
        "offline_set": sorted(OFFLINE_ACTIONS),
        "problems": problems,
    }
    (out / "leads.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 0 if not problems else 1

    setup_stdout()
    print("A10 · 能打同时能证明（`35 §8`）")
    print("-" * 78)
    for r in rows:
        if r.get("ok"):
            print(f"  OK   {r['action']:11s} {r.get('summary')}")
            print(f"       verifiable_by={r.get('verifiable_by')}")
            if r.get("verify_result"):
                print(f"       verify → {r['verify_result']}")
        else:
            print(f"  GATE {r['action']:11s} {r.get('error') or r.get('gate')}")
    for g in gated:
        print(f"  GATE {g['action']:11s} 缺 {g['need']}（沙箱要求 {g['sandbox']}）")
    print("-" * 78)
    print(f"真产出 lead：{len(produced)}/{required}")
    if closure_rate is None:
        print("闭环率      ：统计失败（见上）")
    else:
        print(
            f"闭环率      ：{closure_rate:.2f}"
            f"（{counts.get('verified', 0) + counts.get('refuted', 0)}"
            f"/{sum(counts.values())}）—— 真落库统计，门槛 {CLOSURE_FLOOR:.2f}"
        )
    print("目标        ：lab web-01 单实例 —— act.* 与 verify.* 同一 App/端口（B22）")
    print("沙箱        ：未隔离（本机无 docker/WSL）—— 见账本 B20 open，不冒充")
    print()
    for p in problems:
        print(f"⚠ {p}")
    print()
    print(f"证据：{out / 'leads.json'}")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
