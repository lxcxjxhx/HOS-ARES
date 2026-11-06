#!/usr/bin/env python3
"""B86 · `veripen discover`：目标没有契约时，面从哪来（`H27`）。

三个入口的分工：

| 入口 | 要不要契约 | 面从哪来 | 置信 |
|---|---|---|---|
| ``run`` / ``hunt`` | 要 ``--openapi`` | 契约里的 operations | 0.90（contract） |
| ``discover`` | **不要** | 首页链接/表单，按轮次攒 | 0.70（crawl） |
| ``replay`` | 要 capsule | 已封存的绑定 | 1.00（observed） |

**它不产判定**：这里只回答"这个站有哪些面"，不回答"哪条面有问题"。
攒完的面要进 ``hunt``/``run`` 才谈得上 oracle——而 crawl 置信 0.70 低于契约 0.90，
报告与账本里必须能分开，否则"覆盖了多少"又是假数。

轮次模型见 ``veripen_core.discovery.crawl``：一轮只抓已绑定的面，
新面交回 resolver 下一轮再抓。地址始终只由 resolver 产出（27 N1）。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Sequence

from ._io import setup_stdout

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from veripen_core.discovery import Crawler, Fetcher  # noqa: E402
from veripen_core.discovery.links import Sighting  # noqa: E402
from veripen_core.routes import (  # noqa: E402
    AssetRef,
    Resolver,
    RouteBinding,
    RouteCandidate,
    RouteHints,
    RouteProvenance,
    RouteQuery,
)
from veripen_core.routes.sources.symbols import (  # noqa: E402
    SymbolSource as SymSource,
    SymbolTable as RouteSymbols,
)
from veripen_core.tools import http as http_tool  # noqa: E402

from .cmd_hunt import _host_of, build_guard  # noqa: E402

#: 路径分隔符与基址符号名。基址是**符号值**，不进模板字面量（N3）。
SEP = chr(0x2F)
BASE_SYMBOL = "base"

DEFAULT_MAX_ROUNDS = 2
DEFAULT_MAX_PAGES = 20

#: 闸门的 ``plan`` 是"本次计划允许哪些动作"。发现不是 oracle，但**也必须被允许**——
#: 否则 preflight 会以 plan_denied 拒掉每一次抓取，而失败原因看起来像"网络不通"。
DISCOVER_ACTION = "discover"


def _seed_candidate() -> RouteCandidate:
    """入口面：只有基址，没有路径——首页的那一条。"""
    return RouteCandidate(
        method="GET",
        template="{" + BASE_SYMBOL + "}",
        source=RouteProvenance.CRAWL,
    )


def _candidate_of(sighting: Sighting) -> RouteCandidate:
    """看见的入口 → 候选模板。模板仍是相对形式，基址由符号在解析期补。"""
    return RouteCandidate(
        method=sighting.method,
        template="{" + BASE_SYMBOL + "}" + sighting.path,
        param_names=sighting.params,
        source=RouteProvenance.CRAWL,
    )


def _fetcher() -> Fetcher:
    """把 ``send_body`` 包成 crawler 要的 ``fetch``。**不绕过闸门**。"""

    def _fetch(binding: RouteBinding) -> tuple[int, str] | None:
        try:
            trace, payload = http_tool.send_body(binding, {})
        except Exception:  # noqa: BLE001 - 闸门拒/超时/坏证书都按"这页没拿到"处理
            return None
        return trace.status, payload.decode("utf-8", errors="replace")

    return _fetch


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="veripen discover",
        description="无契约发现：从首页链接/表单攒面（不产判定）",
    )
    ap.add_argument("--target", required=True, help="目标根地址（scheme + host[:port]）")
    ap.add_argument("--max-rounds", type=int, default=DEFAULT_MAX_ROUNDS,
                    help="最多几轮（一轮 = 解析 + 抓 + 抽）")
    ap.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES,
                    help="每轮最多抓几页")
    ap.add_argument("--rate", type=float, default=1.0, help="每秒请求上限（默认 1）")
    ap.add_argument("--burst", type=float, default=2.0, help="突发桶容量（默认 2）")
    ap.add_argument("--out", default="", help="把发现结果写成 JSON（可选）")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    setup_stdout()

    host = _host_of(args.target)
    symbols = RouteSymbols({BASE_SYMBOL: args.target.rstrip(SEP)})
    crawl = Crawler(_fetcher(), max_pages=args.max_pages)

    known: set[str] = set()
    skipped_all: list[tuple[str, str]] = []
    pages = 0
    candidates = [_seed_candidate()]
    truncated = False

    for round_no in range(1, max(1, args.max_rounds) + 1):
        src = SymSource("crawl", RouteProvenance.CRAWL, candidates, symbols, base_symbol=BASE_SYMBOL)
        q = RouteQuery(asset=AssetRef(id=host), hints=RouteHints(), min_confidence=0.0)
        routeset = Resolver(sources=[src]).resolve(q, now=int(time.time()))
        if not routeset.bindings:
            print(f"第 {round_no} 轮：解析出 0 条绑定，停。", file=sys.stderr)
            break

        guard = build_guard(
            host, routeset, (DISCOVER_ACTION,), rate=args.rate, burst=args.burst
        )
        guard.oracle_id = DISCOVER_ACTION
        http_tool.install_guard(guard)
        try:
            result = crawl.step(routeset.bindings)
        finally:
            http_tool.uninstall_guard()

        pages += result.fetched
        fresh = [r.template for r in result.records if r.template not in known]
        known.update(fresh)
        skipped_all.extend(result.skipped)
        truncated = truncated or result.truncated

        note = "（本轮触到页数/记录上限）" if result.truncated else ""
        print(f"第 {round_no} 轮：抓 {result.fetched} 页 / 新面 {len(fresh)} 条{note}")
        if not result.next:
            break
        candidates = [_candidate_of(s) for s in result.next]

    faces = sorted(known)
    print(f"目标：{host}")
    print(f"发现：{len(faces)} 条面 / 抓 {pages} 页 / 置信 0.70（crawl，未验证）")
    for template in faces[:40]:
        print(f"  · {template}")
    if len(faces) > 40:
        print(f"  … 其余 {len(faces) - 40} 条见 --out")
    if skipped_all:
        print(f"跳过：{len(skipped_all)} 条（{skipped_all[0][1]} 等）")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                {
                    "target": host,
                    "confidence": 0.70,
                    "source": RouteProvenance.CRAWL.value,
                    "faces": faces,
                    "pages": pages,
                    "truncated": truncated,
                    "skipped": [{"template": t, "reason": r} for t, r in skipped_all],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"已写出：{out}")

    if not faces:
        print("一条面都没攒到：目标可能不可达、或被闸门挡住（不静默成功）。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
