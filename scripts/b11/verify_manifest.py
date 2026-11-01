#!/usr/bin/env python3
"""B11 门：报告三视图 + 措辞 lint + manifest 校验（一条命令跑完）。

用法：`python scripts/b11/verify_manifest.py <报告目录>`，目录里需有
`report.md` / `report.sarif` / `report.json` / `report.manifest.json`。

任何一条不过即 exit 1——**不降级、不跳过**（09 §7）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from veripen.report import (  # noqa: E402
    FindingView,
    lint_report,
    lint_text,
    verify_manifest,
)


def main(argv: list[str]) -> int:
    if not argv:
        print("用法: verify_manifest.py <报告目录>", file=sys.stderr)
        return 2
    root = Path(argv[0])
    md_path, sarif_path, json_path = root / "report.md", root / "report.sarif", root / "report.json"
    man_path = root / "report.manifest.json"
    missing = [p.name for p in (md_path, sarif_path, json_path, man_path) if not p.exists()]
    if missing:
        print(f"缺少文件：{missing}", file=sys.stderr)
        return 2

    markdown = md_path.read_text(encoding="utf-8")
    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
    js = json.loads(json_path.read_text(encoding="utf-8"))
    manifest = json.loads(man_path.read_text(encoding="utf-8"))

    hits = lint_text(markdown, where="markdown")
    hits += lint_text(json.dumps(sarif, ensure_ascii=False), where="sarif")
    hits += lint_text(json.dumps(js, ensure_ascii=False), where="json")

    ok, problems = verify_manifest(manifest, markdown=markdown, sarif=sarif, js=js)
    for hit in hits:
        print(f"ADDRESS {hit}")
    for problem in problems:
        print(f"MANIFEST {problem}")

    print(f"verify-report: address_hits={len(hits)} manifest_ok={ok} findings={len(js.get('findings', []))}")
    return 0 if (not hits and ok) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
