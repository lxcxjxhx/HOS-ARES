#!/usr/bin/env python3
"""B4 本地演示链：造三条证据 → 写盘 → 校验链 → 六态各跑一遍。

**不联网**：全部走 capsule 内的响应与注入的 routeset。
这是 ``make demo`` 的证据段骨架，B17 的演示脚本直接复用。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from typing import Any
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests" / "unit"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import capsule_fixture as F  # noqa: E402  (需要先修好 sys.path)

from hos_ares_core.evidence import (  # noqa: E402
    ReplayEnv,
    ReplayMode,
    TraceResult,
    chain_capsules,
    dumps_capsule,
    replay,
    save_capsule,
    verify_chain,
)
from hos_ares_core.models import ReplayStatus  # noqa: E402

def _transport(traces: dict[str, TraceResult]) -> Any:
    """把「按 role 查 trace」包成 transport。

    返回 `Any`：`Transport` 是 Protocol，这里只需要 `send` 一个方法，
    标 Protocol 会多一层耦合，而这是演示脚本。
    """

    class _T:
        def send(self, step: Any, binding: Any) -> TraceResult:
            del binding
            return traces[step.role]

    return _T()


def _matching(cap: Any) -> dict[str, TraceResult]:
    """把 capsule 的 steps 折成 {role: TraceResult}，给 `_transport` 用。"""
    out: dict[str, TraceResult] = {
        s.role: TraceResult(status=s.response.status, body_sha256=s.response.body_sha256)
        for s in cap.steps
    }
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out/b4/capsules")
    ap.add_argument("--clean", action="store_true", help="先清空输出目录（默认保留旧证据）")
    args = ap.parse_args(argv)

    out = Path(args.out)
    if args.clean and out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    # `chain_capsules` 原本经 `capsule_fixture` 转手导出，mypy 判「未显式导出」。
    # 直接从 `hos_ares_core` 取——那是它真正的来源（B572）。
    caps = chain_capsules([F.capsule(probe_body=f"demo-{i}") for i in range(3)])
    paths = [save_capsule(c, out) for c in caps]

    for path, cap in zip(paths, caps):
        print(f"sealed {path.name[:16]}… route={cap.route.ref if cap.route else '-'}"
              f" template={cap.route.template_sha256[:12] if cap.route else '-'}")

    report = verify_chain(caps)
    print(f"chain ok={report.ok} head={report.head_sha256[:16]} "
      f"tip={report.tip_sha256[:16]} breaks={len(report.breaks)}")
    if not report.ok:
        for line in report.breaks:
            print(f"  BREAK {line}")
        return 1

    rs = F.routeset()
    ref = F.first_ref(rs)
    target = caps[0]

    cases: list[tuple[str, ReplayEnv, object]] = [
        ("OK", ReplayEnv(now=F.NOW, mode=ReplayMode.LIVE, routeset=rs), _transport(_matching(target))),
        (
            "ROUTE_UNRESOLVED",
            ReplayEnv(now=F.NOW, mode=ReplayMode.LIVE, routeset=F.drop_binding(rs)),
            None,
        ),
        (
            "ROUTE_DRIFT",
            ReplayEnv(
                now=F.NOW,
                mode=ReplayMode.LIVE,
                routeset=F.clone_with_template(rs, ref, F.TEMPLATES["items"], "d" * 64),
            ),
            None,
        ),
        ("BLOCKED", ReplayEnv(now=F.NOW, mode=ReplayMode.LIVE, routeset=rs, scope_in=frozenset()), None),
        ("FAILED", ReplayEnv(now=F.NOW, mode=ReplayMode.LIVE, routeset=rs), _transport({
            **_matching(target),
            "control": TraceResult(status=200, body_sha256="0" * 64),
        })),
        ("PARTIAL", ReplayEnv(now=F.NOW, mode=ReplayMode.LIVE, routeset=rs), _transport({
            **_matching(target),
            "probe": TraceResult(status=200, body_sha256="1" * 64),
        })),
        ("OFFLINE-OK", ReplayEnv(now=F.NOW, mode=ReplayMode.OFFLINE), None),
    ]

    failures = 0
    for expected, env, transport in cases:
        result = replay(target, env, transport)  # type: ignore[arg-type]
        got = result.state.value
        want = expected.replace("OFFLINE-", "")
        mark = "ok " if got == want else "BAD"
        if got != want:
            failures += 1
        print(f"{mark} expect={want:<16} got={got:<16} reason={result.reason}")

    exported = dumps_capsule(target, external=True).decode("utf-8")
    leak = "203.0.113.10" in exported or "http" in exported
    print(f"external export bytes={len(exported)} leak={'YES' if leak else 'no'}")
    return 1 if (failures or leak) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
