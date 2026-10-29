"""`make lint-nodata-stub` —— 禁用 mock 数据的机械检查（`26` 数据真实性政策）。

三类允许来源（`26 §2`）：
  1. `real`        真实靶场录制（capsule/replay 夹具）
  2. `public-snapshot`  公开数据快照（带 sha256 + license + 来源 URL）
  3. `synthetic-managed` 受管拟真集（固定 seed + 人工抽检负责人）

检查什么：
  - 代码里出现 `mock`/`fake`/`dummy`/`stub_data`/`hardcode` 命名的**数据文件或变量**
  - `tests/fixtures/` 下每个 json 必须有 `provenance` 块，且 `source` 属于上述三类
  - `data/` 下每个快照必须有 `manifest.json`，含 sha256 / license / rows

退出码：0 = 干净；1 = 有命中（打印文件:行）。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 允许的数据来源枚举
ALLOWED_SOURCES = {"real", "public-snapshot", "synthetic-managed"}

#: 数据文件里出现这些词即视为可疑（只查数据，不查代码变量名——
#: 代码里 `stub_oracle` 这类测试替身是合法的，见 tests/unit/test_contracts.py）
SUSPECT_FILENAME = re.compile(r"(mock|fake|dummy|sample_data|hardcoded)", re.IGNORECASE)

SCAN_DIRS = ["tests/fixtures", "data"]
SKIP_PARTS = {".git", "__pycache__", ".venv", "node_modules", "plan-veripen", "docs/research"}


def _iter_files(base: Path):
    if not base.exists():
        return
    for p in base.rglob("*"):
        if p.is_file() and not any(part in SKIP_PARTS for part in p.parts):
            yield p


def check_filename_suspects() -> list[str]:
    hits: list[str] = []
    for d in SCAN_DIRS:
        for f in _iter_files(ROOT / d):
            if SUSPECT_FILENAME.search(f.name):
                hits.append(f"{f.relative_to(ROOT)}: filename suggests non-real data")
    return hits


def check_fixture_provenance() -> list[str]:
    """每个夹具 json 必须有 provenance，且 source 合法。"""
    hits: list[str] = []
    fixtures = ROOT / "tests" / "fixtures"
    for f in _iter_files(fixtures):
        if f.suffix.lower() != ".json":
            continue
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            hits.append(f"{f.relative_to(ROOT)}: invalid JSON ({exc})")
            continue
        prov = doc.get("provenance") if isinstance(doc, dict) else None
        if not isinstance(prov, dict):
            hits.append(f"{f.relative_to(ROOT)}: missing top-level 'provenance' block (26 §2)")
            continue
        src = prov.get("source")
        if src not in ALLOWED_SOURCES:
            hits.append(f"{f.relative_to(ROOT)}: provenance.source={src!r} not in {sorted(ALLOWED_SOURCES)}")
        if src == "public-snapshot":
            for key in ("url", "license", "sha256"):
                if not prov.get(key):
                    hits.append(f"{f.relative_to(ROOT)}: public-snapshot requires provenance.{key} (26 §3)")
        if src == "synthetic-managed":
            for key in ("seed", "reviewed_by"):
                if not prov.get(key):
                    hits.append(f"{f.relative_to(ROOT)}: synthetic-managed requires provenance.{key} (26 §3)")
    return hits


def check_data_manifests() -> list[str]:
    """`data/<set>/manifest.json` 必须存在且字段齐全。"""
    hits: list[str] = []
    data_root = ROOT / "data"
    if not data_root.exists():
        return hits
    for sub in sorted(p for p in data_root.iterdir() if p.is_dir()):
        manifest = sub / "manifest.json"
        if not manifest.exists():
            hits.append(f"{sub.relative_to(ROOT)}: missing manifest.json (26 §4)")
            continue
        try:
            doc = json.loads(manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            hits.append(f"{manifest.relative_to(ROOT)}: invalid JSON ({exc})")
            continue
        for key in ("source", "license", "sha256", "rows"):
            if key not in doc:
                hits.append(f"{manifest.relative_to(ROOT)}: missing '{key}' (26 §4)")
        if doc.get("source") not in ALLOWED_SOURCES:
            hits.append(f"{manifest.relative_to(ROOT)}: source={doc.get('source')!r} not allowed")
    return hits


def main() -> int:
    hits = check_filename_suspects() + check_fixture_provenance() + check_data_manifests()
    if hits:
        print(f"lint-nodata-stub: {len(hits)} violation(s)")
        for h in hits:
            print(f"  - {h}")
        return 1
    print("lint-nodata-stub: 0 violations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
