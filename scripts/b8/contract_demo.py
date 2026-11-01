#!/usr/bin/env python3
"""B8 演示：契约 → OperationSet → RouteSet，并打印"哪些面被采纳/哪些在等确认"。

只读本地夹具，不联网、不发请求。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from veripen_contract import parse_openapi, plan  # noqa: E402
from veripen_core.routes import (  # noqa: E402
    AssetRef,
    Resolver,
    RouteHints,
    RouteProvenance,
    RouteQuery,
    StaticSource,
)

HOST = "contract-sample.invalid"


def resolve(opset, confirm):  # noqa: ANN001
    cands = list(plan(opset, confirm=confirm).candidates)
    q = RouteQuery(asset=AssetRef(id="web-01"), hints=RouteHints())
    rs = Resolver(sources=[StaticSource("contract", RouteProvenance.CONTRACT, cands)]).resolve(q, now=1000)
    return rs


def main() -> int:
    sample = (ROOT / "tests" / "fixtures" / "contract" / "sample.json").read_text(encoding="utf-8")
    contract = parse_openapi(sample, asset_id="web-01", allowed_hosts=[HOST])
    print(f"operations={contract.count} opset_sha256={contract.opset_sha256()[:16]}")

    adopted = sum(1 for o in contract.operations if o.tags)
    proposed = sum(1 for o in contract.operations if o.proposed)
    print(f"adopted(contract/self-evident)={adopted} awaiting_operator={proposed}")

    confirm = {"object_read", "protected_read", "profile_read", "file_read", "redirect_entry"}
    first = resolve(contract, confirm)
    second = resolve(contract, confirm)
    print(f"bindings={len(first.bindings)} routeset_sha256={first.routeset_sha256[:16]}")
    print(f"deterministic={first.routeset_sha256 == second.routeset_sha256}")

    for b in first.bindings[:12]:
        print(f"  {b.ref} tags={b.template}")  # 模板只出现在本地调试输出
    print(json.dumps({"bindings": len(first.bindings), "sha": first.routeset_sha256}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
