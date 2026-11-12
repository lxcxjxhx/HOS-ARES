"""B314 · 边界对照：同一批资产在「放行档」与「拒绝档」下的两份账本并排看。

为什么要有这个脚本：`demo-deny` 单独跑只证明"被挡下"，读不出**挡下与放行是同一批资产**。
把两个档并排跑一次，`已验证 2 / 2` 对 `已验证 0 / 2` 才是边界真的在起作用的证据。

用法（需要先起一个回环 lab 靶场，把地址注入 VP_ASSET_WEB_01）：

    python scripts/demo/deny_compare.py --base-url <LAB_BASE_URL> --openapi <SPEC> --out out/deny-cmp

产出：终端一张对照表 + `out/deny-cmp/compare.json`。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ALLOW_PROFILE = "profiles" + chr(0x2F) + "demo-a" + chr(0x2F) + "profile.yaml"
DENY_PROFILE = "profiles" + chr(0x2F) + "demo-deny" + chr(0x2F) + "profile.yaml"


def summarize(findings: Mapping[str, Any]) -> dict[str, Any]:
    """从一份 findings.json 里取出对照要用的那几个数（**纯函数**，便于断言）。"""
    counts = findings.get("counts") or {}
    by_outcome = counts.get("by_outcome") or {}
    reasons = [
        str(f.get("reason_code") or "") for f in (findings.get("findings") or [])
    ]
    return {
        "total": int(counts.get("total") or 0),
        "verified": int(by_outcome.get("reported") or 0),
        "not_run": int(by_outcome.get("not_run") or 0),
        "peer_in_deny": sum(1 for r in reasons if r == "peer_in_deny"),
    }


def render_row(label: str, row: Mapping[str, Any]) -> str:
    return (f"{label:<12}{row['total']:>6}{row['verified']:>8}"
            f"{row['not_run']:>8}{row['peer_in_deny']:>14}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="边界对照：放行档 vs 拒绝档")
    ap.add_argument("--base-url", required=True, help="lab 靶场基址（注入 VP_ASSET_WEB_01）")
    ap.add_argument("--openapi", required=True, help="契约文件")
    ap.add_argument("--out", default=str(ROOT / "out" / "deny-cmp"))
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    os.environ["VP_ASSET_WEB_01"] = args.base_url

    from veripen_cli.cmd_platform import main as platform_main

    rows: dict[str, dict[str, Any]] = {}
    for label, profile in (("demo-a", ALLOW_PROFILE), ("demo-deny", DENY_PROFILE)):
        target = out / label
        rc = platform_main(["--profile", profile, "--openapi", args.openapi,
                            "--out", str(target), "--approve"])
        findings = target / "asset-1" / "findings.json"
        if not findings.exists():
            print(f"{label}: 没产出 findings.json（exit {rc}）", file=sys.stderr)
            return 1
        rows[label] = summarize(json.loads(findings.read_text(encoding="utf-8")))

    header = f"{'档':<12}{'总项':>6}{'已验证':>8}{'未执行':>8}{'peer_in_deny':>14}"
    print(header)
    print("-" * len(header))
    for label, row in rows.items():
        print(render_row(label, row))

    (out / "compare.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    allow, deny = rows["demo-a"], rows["demo-deny"]
    ok = (allow["verified"] > 0 and deny["verified"] == 0
          and deny["peer_in_deny"] == deny["total"])
    print()
    print("对照结论：" + ("边界生效（放行档有验证、拒绝档全被 peer_in_deny 挡下）"
                       if ok else "对不上——两个档的差别不在边界上，查档配置"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
