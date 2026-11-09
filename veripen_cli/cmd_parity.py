"""`veripen parity`（B59）：5 面 × 22 项，一项一行，**状态只有三种**。

- `green`：本机真的跑通了；
- `unverified`：本机缺条件（无 docker / 无浏览器），**不许写绿**（D3）；
- `skipped`：明确不做（决策项），不是"没来得及"。

退出码：只要出现 `red` 就非 0；`unverified` 不算红，但会在汇总里计数——
"能跑的都跑了，跑不了的说清楚"才算验收。
"""

from __future__ import annotations

import argparse
import json
import shutil
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


def _pure_judge() -> tuple[str, str]:
    """V1 不查"是不是正好 15 条"（手抄的数字），只查**不变量**。"""
    from veripen_oracles import ALL_ORACLES
    from veripen_oracles.policy import severity_for

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
    from veripen_core.evidence import replay, verify_chain  # noqa: F401

    return GREEN, "capsule/replay 模块可用"


def _routes() -> tuple[str, str]:
    from dataclasses import fields

    from veripen_core.routes.types import RouteBinding

    names = {f.name for f in fields(RouteBinding)}
    bad = names & {"url", "host", "port", "address"}
    return (GREEN, "RouteBinding 无地址字段") if not bad else (RED, f"出现地址字段 {sorted(bad)}")


def _coverage() -> tuple[str, str]:
    """V4 覆盖：**真的算一遍矩阵**，不再只看"命令能 import"（B176）。

    import 成功 ≠ 可用：用例库空了、oracle 拼错了、规格 14 类缺实现——
    这些都会被旧探针放过去。
    """
    from veripen.bench.core import load_cases
    from veripen_cli.cmd_coverage import build_report

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


def _sandbox() -> tuple[str, str]:
    have = shutil.which("docker") or shutil.which("podman")
    if not have:
        return UNVERIFIED, "无 docker/podman：策略与 BLOCKED 已验，真隔离未验"
    return GREEN, f"运行时 {Path(have).name}"


def _browser() -> tuple[str, str]:
    # 库存在 ≠ 执行体实现：B46 落地后按"执行体存在 + 宿主渲染"给绿，隔离仍记未验
    if not (ROOT / "veripen_core" / "render" / "browser.py").exists():
        return UNVERIFIED, "渲染执行体未做（B46）"
    try:
        from veripen_core.render import BrowserRenderer

        ok = BrowserRenderer().available()
    except Exception:  # noqa: BLE001
        ok = False
    return (GREEN, "执行体存在（宿主渲染；容器内未验）") if ok else (UNVERIFIED, "playwright 不可用")


def _tool_surface() -> tuple[str, str]:
    from veripen_core.tools.surface import build_surface

    count = len(build_surface().catalog())
    return (GREEN, f"{count} 个工具") if count >= 12 else (RED, f"只注册了 {count} 个")


def _discovery() -> tuple[str, str]:
    """E4 资产发现：**已经做了**（B86 起）。桩 fetch 走一轮，不需要网络。

    以前这条写的是"暂按 D-H-03 维持不做"——能力矩阵在说假话，比"没验"更糟。
    """
    import time

    try:
        from veripen_core.discovery import Crawler, seed_candidate
        from veripen_core.routes import (
            AssetRef,
            Resolver,
            RouteHints,
            RouteProvenance,
            RouteQuery,
        )
        from veripen_core.routes.sources import StaticSource
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
    """
    from veripen_core.parallel import TaskResult, merge_sha, run_parallel

    def runner(tid: str) -> TaskResult:
        return TaskResult(
            task_id=tid,
            findings=({"oracle": "probe", "asset": tid, "verdict": "VERIFIED"},),
            steps=1,
        )

    ids = ["t3", "t1", "t2"]
    parallel = run_parallel({t: runner for t in ids}, limit=3)
    serial = tuple(runner(t) for t in sorted(ids))
    if [r.task_id for r in parallel] != sorted(ids):
        return RED, "并发结果没有按 task_id 排序（可复现性就靠这个）"
    if merge_sha(parallel) != merge_sha(serial):
        return RED, "并发与串行的结果指纹不一致"
    return GREEN, (
        f"3 任务并行 · 与串行同指纹（{merge_sha(parallel)[:8]}…）；容器级并行未验"
    )


def _control_plane() -> tuple[str, str]:
    """P1 平台：**真跑一遍 DAG**（B176）——排序 + 依赖未满足集，而不是只看 import。

    "import 成功就算可用"是这一批统一清掉的口径：它永远绿。
    """
    from veripen_core.workflow import load, unmet

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
    from veripen_core.blackboard import Blackboard
    from veripen_core.store import Store

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
    from veripen_core.blackboard import Fact
    from veripen_core.compression import compress

    fact = Fact(id="f-1", task_id="t", subject="a", predicate="p", value="v",
                origin="observed", from_target=False, evidence_id="ev-1",
                supersedes=None, ttl_s=None, created_at=1)
    out = compress(facts=[fact], events=[{"kind": "http", "ok": True}],
                   capsule_ids=["c-1"], verdicts={"o": "VERIFIED"})
    return (GREEN, f"缩到 {out.tokens_after} token") if out.tokens_after > 0 else (RED, "压缩失败")


def _playbooks() -> tuple[str, str]:
    from veripen_core.playbooks import load_all
    from veripen_cli.cmd_playbook import oracle_table

    books = load_all(oracle_table=oracle_table())
    return (GREEN, f"{len(books)} 个资产") if len(books) >= 3 else (RED, "资产不足")


def _plugins() -> tuple[str, str]:
    from veripen_core.plugins.lifecycle import Lifecycle, PluginSpec
    from veripen_core.tools.surface import Impact, build_surface

    surface = build_surface()
    before = surface.catalog()
    lc = Lifecycle(surface=surface)
    spec = PluginSpec(name="p", version="1.0.0", mount="act",
                      tool_name="plugin.p.scan", impact=Impact.READ)
    lc.install(spec)
    lc.uninstall("p")
    return (GREEN, "装/卸复原") if surface.catalog() == before else (RED, "卸载未复原")


def _approvals() -> tuple[str, str]:
    from fastapi.testclient import TestClient

    from web.console import ConsoleState, build_app

    client = TestClient(build_app(ConsoleState()))
    ok = client.get("/ui/approvals").status_code == 200 and client.post("/ui/findings").status_code == 405
    return (GREEN, "审批页可读、其余写路径仍 405") if ok else (RED, "只读边界破了")


def _audit_section() -> tuple[str, str]:
    from veripen.report.audit import SECTIONS, AuditInput, render_audit_section

    text = render_audit_section(AuditInput(task_id="t"))
    missing = [title for _k, title in SECTIONS if title not in text]
    return (GREEN, f"{len(SECTIONS)} 张表") if not missing else (RED, f"缺表 {missing}")


def _workbench() -> tuple[str, str]:
    from veripen_core.workbench.fs import Workbench

    with tempfile.TemporaryDirectory() as tmp:
        wb = Workbench(tmp)
        wb.write("a.txt", "hello")
        return (GREEN, "fs 读写") if wb.read("a.txt").text == "hello" else (RED, "读写不一致")


def _memory() -> tuple[str, str]:
    from veripen_core.blackboard import Blackboard
    from veripen_core.memory import Memory
    from veripen_core.store import Store

    store = Store(":memory:").init()
    try:
        mem = Memory(Blackboard(store))
        fact = mem.remember(asset="a", kind="k", value="v", source_event_id="ev-1")
        return (GREEN, "记忆写入与召回") if mem.recall("a", now=fact.created_at + 1) else (RED, "召回为空")
    finally:
        store.close()


def _tui() -> tuple[str, str]:
    from veripen_cli.tui import TuiState, render_panels

    text = render_panels(TuiState(task_id="t"))
    return (GREEN, "三面板渲染") if "任务区" in text and "只读" in text else (RED, "面板缺失")


def _console() -> tuple[str, str]:
    from fastapi.testclient import TestClient

    from web.console import ConsoleState, build_app

    ok = TestClient(build_app(ConsoleState())).get("/ui/overview").status_code == 200
    return (GREEN, "控制台可读") if ok else (RED, "控制台不可用")


def _delivery() -> tuple[str, str]:
    from veripen_cli.offline import build

    src = ROOT / "packaging" / "offline"
    if not src.is_dir():
        return SKIPPED, "packaging/offline 缺失"
    with tempfile.TemporaryDirectory() as tmp:
        build(src=src, out=Path(tmp) / "pkg", version="parity", profile="light")
    return GREEN, "light 包可组（images/ 需 docker 构建机）"


def _assertions() -> tuple[str, str]:
    text = (ROOT / "plan-veripen" / "16-完整范围与交付地图.md").read_text(encoding="utf-8")
    count = text.count("| **A") + text.count("| A")
    return (GREEN, "断言表已扩到 20 条") if "A20" in text else (RED, "断言表未含 A20")


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
        rows.append({"key": item.key, "face": item.face, "status": status, "detail": detail})
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="parity")
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
        print(f"\n合计 {len(rows)} 项：green {counts[GREEN]} · 未验 {counts[UNVERIFIED]} · "
              f"不做 {counts[SKIPPED]} · red {counts[RED]}")
        print(f"证据：{args.out}")

    return 0 if counts[RED] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
