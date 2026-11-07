#!/usr/bin/env python3
"""V4 复测：无契约发现的可判定率（`H01 V4` 的代理指标）。

**量的不是契约召回**：老指标「真实目标召回 0.33」是"契约里声明的面有多少被判定"，
那要求目标有契约。这里的指标是"发现出来的面有多少能被判定"：

    可判定率 = 有 ≥1 个语义标签的面 / 抓到的面
    可用 oracle 数 = 这些标签能选中的 oracle 数

两者不能混称——前者衡量发现质量，后者衡量契约覆盖。输出里都标明。

用法：
    python scripts/measure_discovery.py --target https://TARGET [--rounds 2] [--pages 10]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from veripen_core.discovery import (  # noqa: E402
    Crawler,
    candidate_of,
    seed_candidate,
    transport_fetcher,
    well_known_candidates,
)
from veripen_core.routes import (  # noqa: E402
    AssetRef,
    Resolver,
    RouteHints,
    RouteProvenance,
    RouteQuery,
)
from veripen_core.routes.sources.base import candidate_from_record  # noqa: E402
from veripen_core.routes.sources.symbols import (  # noqa: E402
    SymbolSource as SymSource,
    SymbolTable as RouteSymbols,
)
from veripen_core.tools import http as http_tool  # noqa: E402
from veripen_oracles import ALL_ORACLES  # noqa: E402
from veripen_cli.cmd_hunt import _host_of, build_guard  # noqa: E402

BASE = "base"
SEP = chr(0x2F)


def _oracles_for(tags: set[str]) -> list[str]:
    picked: list[str] = []
    for cls in ALL_ORACLES:
        selector = getattr(cls.meta, "selector_tags", ())
        if selector and str(selector[0]) in tags and cls.meta.id not in picked:
            picked.append(cls.meta.id)
    return sorted(picked)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="measure_discovery")
    ap.add_argument("--target", required=True)
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--pages", type=int, default=10)
    ap.add_argument("--rate", type=float, default=3.0)
    ap.add_argument("--out", default="")
    ap.add_argument("--render", action="store_true", help="发现时渲染 HTML（慢）")
    args = ap.parse_args(argv)

    host = _host_of(args.target)
    symbols = RouteSymbols({BASE: args.target.rstrip(SEP)})
    import contextlib

    stack = contextlib.ExitStack()
    renderer = None
    if args.render:
        from veripen_core.render.browser import BrowserRenderer

        renderer = stack.enter_context(BrowserRenderer())
    crawler = Crawler(transport_fetcher(), max_pages=args.pages, renderer=renderer)
    candidates = [seed_candidate(BASE), *well_known_candidates(BASE)]
    records = []
    seen: set[tuple[str, str]] = set()
    pages = 0

    def _resolve(cands: list) -> object:
        src = SymSource("crawl", RouteProvenance.CRAWL, cands, symbols, base_symbol=BASE)
        query = RouteQuery(asset=AssetRef(id=host), hints=RouteHints(), min_confidence=0.0)
        return Resolver(sources=[src]).resolve(query, now=int(time.time()))

    for round_no in range(1, max(1, args.rounds) + 1):
        routeset = _resolve(candidates)
        if not routeset.bindings:
            break
        guard = build_guard(host, routeset, ("discover",), rate=args.rate)
        guard.oracle_id = "discover"
        http_tool.install_guard(guard)
        try:
            step = crawler.step(routeset.bindings)
        finally:
            http_tool.uninstall_guard()
        pages += step.fetched
        for rec in step.records:
            key = (rec.method, rec.template)
            if key in seen:
                continue
            seen.add(key)
            records.append(rec)
        if not step.next:
            break
        candidates = [candidate_of(s, BASE) for s in step.next]

    stack.close()
    tags_by_face: dict[str, list[str]] = {}
    for rec in records:
        cand = candidate_from_record(
            rec, source=RouteProvenance.CRAWL, prefix="{" + BASE + "}"
        )
        tags_by_face[rec.template] = list(cand.response_shape.get("semantic_tags") or ())

    tagged = {f: t for f, t in tags_by_face.items() if t}
    tag_counter = Counter(t for tags in tagged.values() for t in tags)
    oracles = _oracles_for(set(tag_counter))
    report = {
        "target": host,
        "metric": "discovery_judgeable_rate",
        "note": "发现召回（有标签的面/抓到的面），不是契约召回",
        "pages_fetched": pages,
        "faces": len(records),
        "faces_tagged": len(tagged),
        "judgeable_rate": round(len(tagged) / len(records), 3) if records else 0.0,
        "tag_counts": dict(tag_counter),
        "oracles_available": oracles,
        "face_tags": tags_by_face,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "face_tags"},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
