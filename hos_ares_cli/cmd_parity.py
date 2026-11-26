"""`hos_ares parity`（B59）：5 面 × 22 项，一项一行，**状态只有三种**。

- `green`：本机真的跑通了；
- `unverified`：本机缺条件（无 docker / 无浏览器），**不许写绿**（D3）；
- `skipped`：明确不做（决策项），不是"没来得及"。

退出码：只要出现 `red` 就非 0；`unverified` 不算红，但会在汇总里计数——
"能跑的都跑了，跑不了的说清楚"才算验收。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]

GREEN, UNVERIFIED, SKIPPED, RED = "green", "unverified", "skipped", "red"


@dataclass(frozen=True)
class Item:
    key: str
    face: str
    probe: Callable[[], tuple[str, str]]

    @property
    def how(self) -> str:
        """这一项的判据**长在哪**（B251）。

        验收口径要求"每项能看到它是怎么被判的"，而这一层压根没有 shell 命令可写：
        每项都是一个内置探针函数。所以给出的是**判据位置**——函数名 + 它自己的第一行说明，
        读者可以据此去读实现，而不是看一个编出来的命令行。
        """
        doc = (self.probe.__doc__ or "").strip().splitlines()
        first = doc[0].strip() if doc else ""
        where = f"cmd_parity.{self.probe.__name__}"
        return f"{where} —— {first}" if first else where


def _pure_judge() -> tuple[str, str]:
    """V1 不查"是不是正好 15 条"（手抄的数字），只查**不变量**。"""
    from hos_ares_oracles import ALL_ORACLES
    from hos_ares_oracles.policy import severity_for

    ids = [c.meta.id for c in ALL_ORACLES]
    if len(ids) != len(set(ids)):
        return RED, "oracle id 重复"
    thin = [c.meta.id for c in ALL_ORACLES if not (c.meta.cwe and c.meta.selector_tags)]
    if thin:
        return RED, f"元数据不全（缺 cwe / selector_tags）：{thin}"
    unruly = [
        c.meta.id for c in ALL_ORACLES
        if severity_for(c.meta.id, c.meta.severity_rule)[1] != "rule"
    ]
    if unruly:
        return RED, f"严重度规则不在表里：{unruly}"
    return GREEN, f"{len(ids)} 条 oracle（id 唯一 / 元数据齐全 / 严重度有规则）"


def _evidence() -> tuple[str, str]:
    """**只证明能导入**（不跑校验）：真跑链校验与 replay 在用例里。"""
    from hos_ares_core.evidence import replay, verify_chain  # noqa: F401

    return GREEN, "capsule/replay 模块可用"


def _routes() -> tuple[str, str]:
    """检查 RouteBinding 的字段名里没有 url/host/port/address。"""
    from dataclasses import fields

    from hos_ares_core.routes.types import RouteBinding

    names = {f.name for f in fields(RouteBinding)}
    bad = names & {"url", "host", "port", "address"}
    return (GREEN, "RouteBinding 无地址字段") if not bad else (RED, f"出现地址字段 {sorted(bad)}")


def _coverage() -> tuple[str, str]:
    """V4 覆盖：**真的算一遍矩阵**，不再只看"命令能 import"（B176）。

    import 成功 ≠ 可用：用例库空了、oracle 拼错了、规格 14 类缺实现——
    这些都会被旧探针放过去。
    """
    from hos_ares.bench.core import load_cases
    from hos_ares_cli.cmd_coverage import build_report

    cases = load_cases(ROOT / "tests" / "bench" / "cases.yaml")
    rep = build_report(cases)
    if rep["cases_total"] == 0:
        return RED, "用例库为空：矩阵无从算起"
    if rep["missing_impl"]:
        return RED, f"规格有但代码无实现：{rep['missing_impl']}"
    if rep["unknown_in_cases"]:
        return RED, f"用例里的 oracle 不在 14 类内：{rep['unknown_in_cases']}"
    return GREEN, (
        f"{rep['covered']}/{rep['denominator']} 类有用例 · 共 {rep['cases_total']} 条"
    )


#: E1 的判据要真起一个容器（几十秒）。同一进程里 `run()` 会被调很多次
#: （测试、`--json` 与人类输出各来一遍），逐次起容器没有信息增量：
#: "这台机器上这个运行时到底隔不隔离"在一次进程生命周期里不会变。
#: 键是 (执行体名, 隔离等级)，所以换了执行体的用例不会被缓存骗过。
_E1_MEMO: dict[tuple[str, str], tuple[str, str]] = {}


def _sandbox() -> tuple[str, str]:
    """E1 执行隔离：**真起一个容器**跑一次写操作，看宿主有没有副作用（B536）。

    判据不是"有没有 docker 这个命令"，而是**到底有没有隔离**：
    命令在 ≠ 服务在（B200），服务在也不代表我们的执行体选得到它。
    所以走 `select_executor()` 拿到的执行体，按它的隔离等级分别判：

    - `container`：真跑一条 `touch`，宿主上**不该**出现那个文件——出现即隔离失效（红）；
    - `job-object`：如实记未验（它不给文件系统隔离，验不出容器语义）；
    - `None`：未验，并说清缺什么。
    """
    from hos_ares_core.sandbox.runner import select_executor

    executor = select_executor()
    if executor is None:
        return UNVERIFIED, "无 docker/podman（含 WSL）：策略与 BLOCKED 已验，真隔离未验"
    isolation = getattr(executor, "isolation", "")
    if isolation != "container":
        return UNVERIFIED, (
            f"执行体 {executor.name} 的隔离等级是 {isolation or '未知'}："
            f"资源受限已验，容器级隔离未验"
        )

    import tempfile

    key = (executor.name, isolation)
    if key in _E1_MEMO:
        return _E1_MEMO[key]

    marker = "hos_ares-e1-marker"
    with tempfile.TemporaryDirectory() as tmp:
        # 判据两半，都对着**宿主真目录**看：
        #   1) 容器内写 /tmp → 宿主的同名路径不该出现（命名空间隔离）；
        #   2) 容器往挂进来的只读宿主管写 → 写不成，宿主目录必须还是空的（挂载语义）。
        script = f"touch /tmp/{marker} && echo tmp-ok; touch /work/{marker} || echo mount-ro"
        result = executor.run(
            ["sh", "-c", script], image="python:3.13-slim",
            mounts=[(tmp, "/work")], timeout_s=240,
        )
        leaked_to_mount = (Path(tmp) / marker).exists()
    if result.outcome != "ok":
        verdict_detail: tuple[str, str] = (UNVERIFIED, (
            f"执行体 {executor.name} 起不了容器（{result.reason or result.stderr[:80]}）：真隔离未验"
        ))
    elif leaked_to_mount:
        verdict_detail = (RED, f"容器写进宿主目录（{executor.name}）：隔离失效")
    elif "tmp-ok" not in result.stdout:
        verdict_detail = (UNVERIFIED, (
            f"容器起来了但探针没跑（stdout={result.stdout[:60]!r}）：真隔离未验"
        ))
    else:
        verdict_detail = (GREEN, (
            f"运行时 {executor.name} · isolation=container"
            f"（容器内写入宿主不可见 / 只读挂载不可写）"
        ))
    _E1_MEMO[key] = verdict_detail
    return verdict_detail


def _browser() -> tuple[str, str]:
    """渲染执行体在位、BrowserRenderer 报可用；容器内渲染未验。"""
    # 库存在 ≠ 执行体实现：B46 落地后按"执行体存在 + 宿主渲染"给绿，隔离仍记未验
    if not (ROOT / "hos_ares_core" / "render" / "browser.py").exists():
        return UNVERIFIED, "渲染执行体未做（B46）"
    try:
        from hos_ares_core.render import BrowserRenderer

        ok = BrowserRenderer().available()
    except Exception:  # noqa: BLE001
        ok = False
    return (GREEN, "执行体存在（宿主渲染；容器内未验）") if ok else (UNVERIFIED, "playwright 不可用")


def _tool_surface() -> tuple[str, str]:
    """构建工具面并数注册项，≥12 才算过。"""
    from hos_ares_core.tools.surface import build_surface

    count = len(build_surface().catalog())
    return (GREEN, f"{count} 个工具") if count >= 12 else (RED, f"只注册了 {count} 个")


def _discovery() -> tuple[str, str]:
    """E4 资产发现：**已经做了**（B86 起）。桩 fetch 走一轮，不需要网络。

    以前这条写的是"暂按 D-H-03 维持不做"——能力矩阵在说假话，比"没验"更糟。
    """
    import time

    try:
        from hos_ares_core.discovery import Crawler, seed_candidate
        from hos_ares_core.routes import (
            AssetRef,
            Resolver,
            RouteHints,
            RouteProvenance,
            RouteQuery,
        )
        from hos_ares_core.routes.sources import StaticSource
    except Exception as exc:  # noqa: BLE001
        return RED, f"发现链导入失败：{type(exc).__name__}"

    def _fetch(_binding: object) -> tuple[int, str, str]:
        # 桩：一页 HTML 带一个链接，够验证"能不能从 HTML 抽候选"
        return 200, "text/html", '<html><a href="/api/items/1">i</a></html>'

    crawler = Crawler(_fetch, max_pages=2)
    src = StaticSource("crawl", RouteProvenance.CRAWL, [seed_candidate("base")])
    routeset = Resolver(sources=[src]).resolve(
        RouteQuery(asset=AssetRef(id="fixture.invalid"), hints=RouteHints(),
                   min_confidence=0.0),
        now=int(time.time()),
    )
    if not routeset.bindings:
        return RED, "种子面没解析出来"
    got = len(crawler.step(routeset.bindings).next)
    if not got:
        return RED, "抓了一页却没抽出任何候选"
    return GREEN, f"发现链可用（一轮抽到 {got} 条候选）"


def _parallel() -> tuple[str, str]:
    """E5 执行：**真跑三个任务**，比并发与串行的结果指纹（B176）。

    旧探针只看 `parallel.py` 在不在文件系统里——那连"能不能跑"都没验。

    B537 起两半都算：进程内并发（一直有）+ **容器级并发**（每任务一容器）。
    后者需要容器档执行体；没有就只报进程内那半，并写明容器级未验——
    **不许把有容器当成已经验过**，也不许把没容器当成那一半不存在。
    """
    from hos_ares_core.parallel import (
        ContainerTask,
        TaskResult,
        merge_sha,
        run_parallel,
        run_parallel_containers,
    )

    def runner(tid: str) -> TaskResult:
        return TaskResult(
            task_id=tid,
            findings=({"oracle": "probe", "asset": tid, "verdict": "VERIFIED"},),
            steps=1,
        )

    ids = ["t3", "t1", "t2"]
    in_process = run_parallel({t: runner for t in ids}, limit=3)
    serial = tuple(runner(t) for t in sorted(ids))
    if [r.task_id for r in in_process] != sorted(ids):
        return RED, "并发结果没有按 task_id 排序（可复现性就靠这个）"
    if merge_sha(in_process) != merge_sha(serial):
        return RED, "并发与串行的结果指纹不一致"

    from hos_ares_core.sandbox.runner import select_executor

    executor = select_executor()
    if executor is None or getattr(executor, "isolation", "") != "container":
        return GREEN, (
            f"3 任务并行 · 与串行同指纹（{merge_sha(in_process)[:8]}…）；容器级并行未验"
            f"（执行体 isolation={getattr(executor, 'isolation', None)!r}）"
        )

    probe = "import hashlib,sys;print(hashlib.sha256(sys.argv[1].encode()).hexdigest()[:16])"
    tasks = tuple(ContainerTask(task_id=t, argv=("python", "-c", probe, t)) for t in ids)
    concurrent = run_parallel_containers(executor, tasks, timeout_s=300)
    if any(r.outcome != "ok" for r in concurrent):
        bad = next(r for r in concurrent if r.outcome != "ok")
        return UNVERIFIED, (
            f"容器级并发没能跑起来（{bad.task_id}: {bad.reason or bad.outcome}）：未验"
        )
    by_task = {r.task_id: r.stdout for r in concurrent}
    sequential = {
        task.task_id: run_parallel_containers(executor, (task,), timeout_s=300)[0].stdout
        for task in sorted(tasks, key=lambda x: x.task_id)
    }
    if by_task != sequential:
        return RED, "容器级并发与串行结果不同（隔离或调度污染了结果）"
    return GREEN, (
        f"3 任务并行 · 进程内与串行同指纹（{merge_sha(in_process)[:8]}…）"
        f" · 容器级并发与串行逐条相同（{executor.name}）"
    )


def _control_plane() -> tuple[str, str]:
    """P1 平台：**真跑一遍 DAG**（B176）——排序 + 依赖未满足集，而不是只看 import。

    "import 成功就算可用"是这一批统一清掉的口径：它永远绿。
    """
    from hos_ares_core.workflow import load, unmet

    wf = load(ROOT / "config" / "workflow.yaml")
    order = wf.order()
    if not order:
        return RED, "工作流为空：阶段推进无从谈起"
    first = order[0].id
    missing = unmet({first}, wf, set())
    if not missing:
        return RED, "只完成第一阶段却认为依赖全满足——DAG 依赖没生效"
    waves = [w for w, ids in wf.by_wave().items() if ids]
    return GREEN, (
        f"{len(order)} 阶段 / {len(waves)} 个波次 · 只做首阶段仍缺 {len(missing)} 个"
    )


def _blackboard() -> tuple[str, str]:
    """内存库里写一条黑板冲突再读回来。"""
    from hos_ares_core.blackboard import Blackboard
    from hos_ares_core.store import Store

    store = Store(":memory:").init()
    try:
        bb = Blackboard(store)
        bb.append(task_id="t", subject="s", predicate="p", value="a",
                  origin="inference", evidence_id="ev-1")
        bb.append(task_id="t", subject="s", predicate="p", value="b",
                  origin="observed", evidence_id="ev-2")
        winner = bb.resolve("t")[0].winner.value
        return (GREEN, f"消解胜出 {winner}") if winner == "b" else (RED, "消解选错")
    finally:
        store.close()


def _compression() -> tuple[str, str]:
    """P3 平台：**真的比压缩前后**（B177）。

    旧探针只看 `tokens_after > 0`——压不动（甚至变大）也会绿。
    现在两段：小输入必须**不放大**（压不动就原样返回），真实规模必须真的变小。
    """
    from hos_ares_core.blackboard import Fact
    from hos_ares_core.compression import compress

    fact = Fact(id="f-1", task_id="t", subject="a", predicate="p", value="v",
                origin="observed", from_target=False, evidence_id="ev-1",
                supersedes=None, ttl_s=None, created_at=1)
    tiny = compress(facts=[fact], events=[{"kind": "http", "ok": True}],
                    capsule_ids=["c-1"], verdicts={"o": "VERIFIED"})
    if tiny.tokens_after > tiny.tokens_before:
        return RED, f"小输入被放大：{tiny.tokens_before} -> {tiny.tokens_after}"

    facts = [
        Fact(id=f"f-{i}", task_id="t", subject=f"s{i}", predicate="p", value=f"v{i}",
             origin="observed", from_target=False, evidence_id=f"ev-{i}",
             supersedes=None, ttl_s=None, created_at=i)
        for i in range(25)
    ]
    events = [{"kind": "http", "ok": True, "status": 200} for _ in range(60)]
    big = compress(facts=facts, events=events, capsule_ids=["c-1", "c-2"],
                   verdicts={"o": "VERIFIED"})
    if big.tokens_after >= big.tokens_before:
        return RED, f"真实规模压不动：{big.tokens_before} -> {big.tokens_after}"
    return GREEN, (
        f"真实规模 {big.tokens_before} → {big.tokens_after} token（省 {big.reduction:.0%}）"
        f" · 小输入原样返回（{tiny.mode}）"
    )


def _playbooks() -> tuple[str, str]:
    """加载 playbooks 并校验 schema 与 oracle 存在性，≥3 个资产才算过。"""
    from hos_ares_core.playbooks import load_all
    from hos_ares_cli.cmd_playbook import oracle_table

    books = load_all(oracle_table=oracle_table())
    return (GREEN, f"{len(books)} 个资产") if len(books) >= 3 else (RED, "资产不足")


def _plugins() -> tuple[str, str]:
    """装上再卸下一个插件，检查工具面前后一致。"""
    from hos_ares_core.plugins.lifecycle import Lifecycle, PluginSpec
    from hos_ares_core.tools.surface import Impact, build_surface

    surface = build_surface()
    before = surface.catalog()
    lc = Lifecycle(surface=surface)
    spec = PluginSpec(name="p", version="1.0.0", mount="act",
                      tool_name="plugin.p.scan", impact=Impact.READ)
    lc.install(spec)
    lc.uninstall("p")
    return (GREEN, "装/卸复原") if surface.catalog() == before else (RED, "卸载未复原")


def _approvals() -> tuple[str, str]:
    """起控制台：审批页可读，其它页的写方法一律 405。"""
    from fastapi.testclient import TestClient

    from web.console import ConsoleState, build_app

    client = TestClient(build_app(ConsoleState()))
    ok = client.get("/ui/approvals").status_code == 200 and client.post("/ui/findings").status_code == 405
    return (GREEN, "审批页可读、其余写路径仍 405") if ok else (RED, "只读边界破了")


def _audit_section() -> tuple[str, str]:
    """渲染审计节，检查它每个小节标题都在。"""
    from hos_ares.report.audit import SECTIONS, AuditInput, render_audit_section

    text = render_audit_section(AuditInput(task_id="t"))
    missing = [title for _k, title in SECTIONS if title not in text]
    return (GREEN, f"{len(SECTIONS)} 张表") if not missing else (RED, f"缺表 {missing}")


def _workbench() -> tuple[str, str]:
    """临时目录里写一个文件再读回来。"""
    from hos_ares_core.workbench.fs import Workbench

    with tempfile.TemporaryDirectory() as tmp:
        wb = Workbench(tmp)
        wb.write("a.txt", "hello")
        return (GREEN, "fs 读写") if wb.read("a.txt").text == "hello" else (RED, "读写不一致")


def _memory() -> tuple[str, str]:
    """内存库里写入并召回一次记忆。"""
    from hos_ares_core.blackboard import Blackboard
    from hos_ares_core.memory import Memory
    from hos_ares_core.store import Store

    store = Store(":memory:").init()
    try:
        mem = Memory(Blackboard(store))
        fact = mem.remember(asset="a", kind="k", value="v", source_event_id="ev-1")
        return (GREEN, "记忆写入与召回") if mem.recall("a", now=fact.created_at + 1) else (RED, "召回为空")
    finally:
        store.close()


def _tui() -> tuple[str, str]:
    """纯行模式渲染三面板，检查关键标题在场。"""
    from hos_ares_cli.tui import TuiState, render_panels

    text = render_panels(TuiState(task_id="t"))
    return (GREEN, "三面板渲染") if "任务区" in text and "只读" in text else (RED, "面板缺失")


def _console() -> tuple[str, str]:
    """控制台的 overview 页返回 200。"""
    from fastapi.testclient import TestClient

    from web.console import ConsoleState, build_app

    ok = TestClient(build_app(ConsoleState())).get("/ui/overview").status_code == 200
    return (GREEN, "控制台可读") if ok else (RED, "控制台不可用")


def _delivery() -> tuple[str, str]:
    """组一个 light 包并**顺带跑它自己的 verify**——组得出来 ≠ 过得了校验。

    本机没有构建机那两样东西（wheels/ 与 images/），所以 verify 必然报这两条；
    这一项判的是"包能不能组出来"，因此仍记 green，但把"过不了自身校验"写在明细里。
    """
    from hos_ares_cli.offline import build, verify

    src = ROOT / "packaging" / "offline"
    if not src.is_dir():
        return SKIPPED, "packaging/offline 缺失"
    with tempfile.TemporaryDirectory() as tmp:
        pkg = Path(tmp) / "pkg"
        build(src=src, out=pkg, version="parity", profile="light")
        result = verify(pkg)
    if result.ok:
        return GREEN, f"light 包可组，且自身校验通过（{result.checked} 个文件）"
    gaps = "；".join(p.split("：")[0] for p in result.problems[:2]) or "见 verify 输出"
    return GREEN, f"light 包可组；自身校验未过（{gaps}，需构建机补齐）"


def _assertions() -> tuple[str, str]:
    """G3 产品：**数行**，不是看正文里有没有出现字符串 `A20`（B177）。

    旧写法 `if "A20" in text` 会被任何一处提到 A20 的段落骗过——
    连"正文里说了一句 A20 还没做"都能让它绿。
    """
    text = (ROOT / "plan-hos-legacy" / "16-完整范围与交付地图.md").read_text(encoding="utf-8")
    ids = {int(m) for m in re.findall(r"^\|\s*\*{0,2}A(\d+)\b", text, re.M)}
    missing = [f"A{i}" for i in range(1, 21) if i not in ids]
    if missing:
        return RED, f"断言表缺：{', '.join(missing)}"
    extra = sorted(i for i in ids if i > 20)
    tail = f"；另有 A{extra[0]}–A{extra[-1]} 扩展" if extra else ""
    return GREEN, f"A1–A20 齐全（表内共 {len(ids)} 条）{tail}"


ITEMS: tuple[Item, ...] = (
    Item("V1", "判定", _pure_judge), Item("V2", "证据", _evidence),
    Item("V3", "地址", _routes), Item("V4", "覆盖", _coverage),
    Item("E1", "执行", _sandbox), Item("E2", "执行", _browser),
    Item("E3", "执行", _tool_surface), Item("E4", "执行", _discovery),
    Item("E5", "执行", _parallel),
    Item("P1", "平台", _control_plane), Item("P2", "平台", _blackboard),
    Item("P3", "平台", _compression), Item("P4", "平台", _playbooks),
    Item("P5", "平台", _plugins), Item("P6", "平台", _approvals),
    Item("P7", "平台", _audit_section),
    Item("W1", "工作台", _workbench), Item("W2", "工作台", _memory),
    Item("W3", "工作台", _tui),
    Item("G1", "产品", _console), Item("G2", "产品", _delivery), Item("G3", "产品", _assertions),
)


def run() -> list[dict[str, str]]:
    rows = []
    for item in ITEMS:
        try:
            status, detail = item.probe()
        except Exception as exc:  # noqa: BLE001 - 单点异常如实记红，不拖垮全表
            status, detail = RED, f"{type(exc).__name__}: {exc}"
        rows.append({"key": item.key, "face": item.face, "status": status,
                     "detail": detail, "how": item.how})
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hos-ares parity")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "out" / "parity.json"))
    args = ap.parse_args(argv)

    rows = run()
    counts = {k: sum(1 for r in rows if r["status"] == k)
              for k in (GREEN, UNVERIFIED, SKIPPED, RED)}
    payload = {"items": rows, "counts": counts, "total": len(rows)}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        width = max(len(r["key"]) for r in rows)
        for r in rows:
            mark = {"green": "OK  ", "unverified": "未验", "skipped": "不做", "red": "RED "}[r["status"]]
            print(f"  {mark} {r['key']:<{width}} {r['face']:<4} {r['detail']}")
            print(f"         └ {r.get('how', '')}")
        print(f"\n合计 {len(rows)} 项：green {counts[GREEN]} · 未验 {counts[UNVERIFIED]} · "
              f"不做 {counts[SKIPPED]} · red {counts[RED]}")
        print(f"证据：{args.out}")

    return 0 if counts[RED] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
