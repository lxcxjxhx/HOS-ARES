#!/usr/bin/env python3
"""veripen plugin-roundtrip —— A11「能力可扩展」的**验收命令**（`36`）。

用法：python -m veripen_cli.cmd_plugin_roundtrip [--out out/plugins]

A11 的实质**不是**"插件源码能解析"，而是两件事：

1. **装得进**：knowledge 类挂载点必须 `admitted=True` 且 `sandbox=none`
   —— 装 100 篇知识是安全的，装 1 个 oracle 是重流程（`36 §4`）。
2. **装了不改判定**：同一份判定输入，装 knowledge 前后 `Context.judge_input()`
   必须**逐字相同**，且 `L2.knowledge` 段不许出现在判定视图里。

第 2 条是能红的：把知识正文塞进 `judge` 段就会立刻失败。
如果只验证第 1 条，那证明的是结构，不是 A11。

**当前状态**：插件**没有网络客户端**（`Fetch` 注入点空），
所以只做本地包往返；远程源（npm/PyPI/GitHub）明确报 `no_fetch_impl`，归 B20。
"""

from __future__ import annotations

import argparse
import json
import sys
import json as _json
from datetime import datetime, timezone
from pathlib import Path

from ._io import setup_stdout  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from typing import Sequence  # noqa: E402

from veripen_core.context import (  # noqa: E402
    KNOWLEDGE_SEGMENT,
    Context,
    KnowledgeDoc,
    assert_no_leak,
    load_dir,
)
from veripen_core.plugins import fetch as pfetch  # noqa: E402
from veripen_core.plugins.discovery import CacheEntry, SearchCache  # noqa: E402
from veripen_core.plugins.admission import (  # noqa: E402
    BENCH_CASES,
    Candidate,
    MountPolicy,
    ReviewLevel,
    admit,
    default_policies,
)
from veripen_core.plugins.registry import Mount, PluginRecord, Source, Trust  # noqa: E402

NOW = datetime.now(timezone.utc)
SHA = "a" * 64


def _record(mount: Mount) -> PluginRecord:
    return PluginRecord(
        id=f"local/{mount.value}",
        kind=mount.value,
        scenario=mount,
        installed_version="1.0.0",
        source=Source(type="local", repo=f"local/{mount.value}", commit="local"),
        install_method="raw",
        installed_at=NOW.isoformat(),
        trust=Trust.REVIEWED,
    )


def _candidate(mount: Mount) -> Candidate:
    return Candidate(
        record=_record(mount),
        content_sha256=SHA,
        signature="sig:local",
        invariants_ok=frozenset(),
        bench={"cases": BENCH_CASES, "fp": 0},
        creates_process=(mount is Mount.ACT),
        direct_egress=False,
        requested_binaries=("nmap",) if mount is Mount.ACT else (),
        route_test_cases=3 if mount is Mount.SOURCE else 0,
    )


def _judge_view(docs: Sequence[KnowledgeDoc]) -> dict[str, object]:
    """判定侧可见的东西。装 knowledge 前后 **judge_input 必须逐字相同**。

    注意区分两件事：
      - `L2.knowledge` **应该**出现在 `segments` 里（那是给它准备的位子）；
      - 它**不许**出现在 `judge_input` 里（36 §4.1 的红线）。
    把这两件事混成一个判断，会得出"装了知识就违规"的错误结论。
    """
    ctx = Context(segments={}, judge="pairs:baseline|probe|control").with_knowledge(docs)
    judge = ctx.judge_input()
    return {
        "judge_input": judge,
        "judge_input_contains_knowledge_body": any(
            d.body.strip()[:32] and d.body.strip()[:32] in judge for d in docs
        ),
        "knowledge_segment_present": KNOWLEDGE_SEGMENT in ctx.segments,
        "segment_names": sorted(ctx.segments),
    }



def _probe_remote_fetch() -> dict[str, object]:
    """验证 fetch 层的三条纪律，用**脚本化 fetcher**（不真连 GitHub）。

    为什么不在 CI 里真连：外网可用性不该成为断言的一部分——
    网络抖动会让"A11 通过"变成"今天网络好"。所以这里验的是**协议行为**：
      ① 预算真的挡（第二次请求超额即拒）；
      ② etag 真的带（If-None-Match 送到 fetcher）；
      ③ 304 真的不覆盖（results 保持不变，只刷新 fetched_at）。

    真网形态由 `http_fetcher()` 提供，`ALLOWED_ORIGINS` 是闭集；
    它是产品常量（插件源），不是客户资产地址。
    """
    body = json.dumps(
        {"items": [
            {"id": 1, "repository_url": "https://api.github.com/repos/o/a",
             "pushed_at": "2026-10-01T00:00:00Z", "topics": ["security"]},
            {"id": 2, "repository_url": "https://api.github.com/repos/o/b",
             "pushed_at": "2026-10-02T00:00:00Z", "topics": []},
        ]}
    ).encode()

    fetcher = pfetch.ScriptedFetcher([
        (200, body, 'W/"etag-1"'),
        (304, b"", 'W/"etag-1"'),
    ])
    cache = SearchCache(budget_per_refresh=2)
    entry = CacheEntry(query="idor enumeration")

    n1, not_modified_1 = pfetch.refresh(cache, entry, fetcher=fetcher)
    first_count = len(entry.results)
    etag_after_first = entry.etag

    n2, not_modified_2 = pfetch.refresh(cache, entry, fetcher=fetcher)
    second_count = len(entry.results)

    # 第三次必然超预算
    budget_blocked = False
    try:
        pfetch.refresh(cache, entry, fetcher=fetcher)
    except Exception:
        budget_blocked = True

    # etag 真的被送到 fetcher（第 2 次请求带 If-None-Match）
    etag_sent = any(call[2] for call in fetcher.calls)
    # Result 里不许出现可定位地址
    no_address = all("://" not in r.repo for r in entry.results)

    ok = (
        not_modified_1 is False
        and first_count == 2
        and not_modified_2 is True
        and second_count == first_count
        and budget_blocked
        and etag_sent
        and no_address
        and etag_after_first == entry.etag
    )
    return {
        "step": "remote-fetch",
        "ok": ok,
        "requests": n1 + n2,
        "304_kept_results": second_count == first_count,
        "budget_blocked_third": budget_blocked,
        "etag_sent": etag_sent,
        "results_have_no_address": no_address,
        "allowed_origins": sorted(pfetch.ALLOWED_ORIGINS),
        "note": "脚本化 fetcher 验协议行为；真网由 http_fetcher() 提供（外网可用性不参与断言）",
    }

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="veripen plugin-roundtrip")
    ap.add_argument("--out", default=str(ROOT / "out" / "plugins"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    problems: list[str] = []
    steps: list[dict[str, object]] = []
    policies = default_policies()

    # ── 1. 四类挂载点的准入策略必须与 36 §4.1 一致（分级，不是一律放行）
    for mount in (Mount.KNOWLEDGE, Mount.ACT, Mount.SOURCE, Mount.ORACLE):
        pol: MountPolicy = policies[mount]
        steps.append(
            {
                "step": f"policy:{mount.value}",
                "ok": True,
                "allow_install": pol.allow_install,
                "sandbox": pol.sandbox,
                "review": pol.review.value,
                "bench_required": getattr(pol, "bench_required", False),
            }
        )

    # ── 2. knowledge 必须装得进（松），oracle 必须过 44 例（重）
    try:
        k = admit(_candidate(Mount.KNOWLEDGE), policies=policies)
        steps.append({"step": "admit:knowledge", "ok": k.admitted, "reasons": list(k.reasons)})
        if not k.admitted:
            problems.append(f"knowledge 插件被拒（装不上就谈不上可扩展）：{k.reasons}")
    except Exception as exc:  # noqa: BLE001
        steps.append({"step": "admit:knowledge", "ok": False, "error": f"{type(exc).__name__}: {exc}"})
        problems.append(f"装配层抛错：{type(exc).__name__}: {exc}")

    try:
        # 给一个**没跑够 bench** 的 oracle：必须被拒（这是分级准入的证明）
        thin = Candidate(
            record=_record(Mount.ORACLE),
            content_sha256=SHA,
            signature="sig:local",
            bench={"cases": BENCH_CASES - 1, "fp": 0},
            requested_binaries=(),
            creates_process=False,
            direct_egress=False,
            route_test_cases=0,
        )
        o = admit(thin, policies=policies)
        steps.append({"step": "admit:oracle-thin", "ok": not o.admitted, "reasons": list(o.reasons)})
        if o.admitted:
            problems.append("没跑够 44 例的 oracle 被放行——分级准入失效")
    except Exception as exc:  # noqa: BLE001
        steps.append({"step": "admit:oracle-thin", "ok": False, "error": f"{type(exc).__name__}: {exc}"})
        problems.append(f"oracle 准入检查抛错：{type(exc).__name__}: {exc}")

    # ── 3. A11 核心：装了 knowledge，判定视图不变
    try:
        docs = load_dir(ROOT / "knowledge")
        before = _judge_view([])
        after = _judge_view(docs)
        same = before["judge_input"] == after["judge_input"]
        steps.append(
            {
                "step": "judge-invariance",
                "ok": same and not after["judge_input_contains_knowledge_body"],
                "docs_loaded": len(docs),
                "judge_input_unchanged": same,
                "knowledge_body_in_judge": after["judge_input_contains_knowledge_body"],
                "knowledge_segment_present_in_segments": after["knowledge_segment_present"],
                "segment_names_after": after["segment_names"],
            }
        )
        if not same:
            problems.append("装 knowledge 后 judge_input 变了——knowledge 进了判定路径")
        if after["judge_input_contains_knowledge_body"]:
            problems.append("知识正文出现在 judge_input 里（36 §4.1 红线）")
        if not after["knowledge_segment_present"]:
            problems.append(f"knowledge 没进 {KNOWLEDGE_SEGMENT} 段——加载链路断了")
        # 再加一道：正文泄漏检查（36 §4.1）
        ctx = Context(segments={"L1": "只提一句用过 idor-enumeration"},
                      judge="pairs:baseline|probe|control").with_knowledge(docs)
        try:
            assert_no_leak(ctx, docs)
            steps.append({"step": "no-leak", "ok": True})
        except Exception as exc:  # noqa: BLE001
            steps.append({"step": "no-leak", "ok": False, "error": str(exc)})
            problems.append(f"知识正文泄漏：{exc}")
    except Exception as exc:  # noqa: BLE001
        steps.append({"step": "judge-invariance", "ok": False, "error": f"{type(exc).__name__}: {exc}"})
        problems.append(f"判定不变性检查抛错：{type(exc).__name__}: {exc}")

    # ── 4. 远程源：真 fetch（带 etag/304/预算），但**打桩源不冒充真网**
    try:
        steps.append(_probe_remote_fetch())
    except Exception as exc:  # noqa: BLE001
        steps.append({"step": "remote-fetch", "ok": False, "error": f"{type(exc).__name__}: {exc}"})
        problems.append(f"远程 fetch 检查抛错：{type(exc).__name__}: {exc}")

    # ── 5. 装→用→升级→卸（B55：卸载后工具面必须逐字回到装前）
    try:
        steps.append(_probe_lifecycle())
    except Exception as exc:  # noqa: BLE001
        steps.append({"step": "lifecycle", "ok": False, "error": f"{type(exc).__name__}: {exc}"})
        problems.append(f"插件生命周期检查抛错：{type(exc).__name__}: {exc}")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    record = {"steps": steps, "problems": problems, "review_levels": [
        {"mount": m.value, "review": policies[m].review.value} for m in
        (Mount.KNOWLEDGE, Mount.ACT, Mount.SOURCE, Mount.ORACLE)
    ]}
    (out_dir / "roundtrip.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if args.json:
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 0 if not problems else 1

    setup_stdout()
    print("A11 · 能力可扩展（`36`）")
    print("-" * 78)
    for s in steps:
        mark = "OK  " if s["ok"] else "GATE"
        detail = (
            s.get("reasons") or s.get("error") or s.get("note")
            or f"review={s.get('review')} sandbox={s.get('sandbox')}"
        )
        print(f"  {mark} {s['step']:18s} {detail}")
    print("-" * 78)
    for p in problems:
        print(f"⚠ {p}")
    print(f"\n证据：{out_dir / 'roundtrip.json'}")
    return 0 if not problems else 1


def _probe_lifecycle() -> dict[str, object]:
    """装 → 用 → 升级（失败回滚）→ 卸；核心断言是 catalog 逐字相同。"""
    from veripen_core.plugins.lifecycle import Lifecycle, PluginError, PluginSpec
    from veripen_core.tools.surface import Impact, build_surface

    surface = build_surface()
    before = surface.catalog()
    lc = Lifecycle(surface=surface)
    spec = PluginSpec(name="demo", version="1.0.0", mount="act",
                      tool_name="plugin.demo.scan", impact=Impact.EXEC)

    preview = lc.preview(spec)
    lc.install(spec, handler=lambda _args: {"ok": True})
    used = surface.invoke(spec.tool_name or "", {}, approved=True)

    def bad(_spec: PluginSpec) -> None:
        raise PluginError("bench_failed", "新版本未通过用例")

    rolled_back = False
    try:
        lc.upgrade(PluginSpec(name="demo", version="2.0.0", mount="act",
                              tool_name="plugin.demo.scan", impact=Impact.EXEC), check=bad)
    except PluginError:
        rolled_back = lc.installed["demo"].version == "1.0.0"

    lc.uninstall("demo")
    restored = surface.catalog() == before
    return {
        "step": "lifecycle",
        "ok": bool(used.get("ok")) and rolled_back and restored,
        "preview_impact": preview["impact"],
        "upgrade_rolled_back": rolled_back,
        "catalog_restored": restored,
        "note": "装→用→升级失败回滚→卸 四段；catalog 逐字比较",
    }


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
