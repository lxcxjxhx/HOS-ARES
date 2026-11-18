"""B255 · 平台汇总报告的"原因"列也要说人话（同一件事的第三处）。

改前实测（真跑 platform，两资产）：

```
| hos_ares/path_traversal/CWE-22 | undetermined | symbol_missing:SAFE_FILE | - |
| hos_ares/xss_dom/CWE-79 | not_run | marker_not_fired | - |
```

而同一轮产出的 `hunt.md` / 各资产 `report.md` 在 B248 已经写成人话。同一份交付里
两种写法，读者会以为平台表是另一套口径。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

SEP = chr(0x2F)


def _spec(base: str) -> str:
    paths = {
        f"{SEP}api{SEP}search": {"get": {"x-hos_ares-tags": ["param_injectable"]}},
        f"{SEP}api{SEP}read": {"get": {"x-hos_ares-tags": ["file_read"]}},
    }
    return json.dumps({"openapi": "3.0.0", "info": {"title": "lab", "version": "1"},
                       "servers": [{"url": base}], "paths": paths})


@pytest.fixture()
def platform_report(tmp_path: Path) -> str:
    from lab.app import App
    from lab.net_server import LabServer
    from hos_ares_cli.cmd_platform import main as platform_main

    a, b = LabServer(App(secure=False)).start(), LabServer(App(secure=False)).start()
    try:
        spec = tmp_path / "spec.json"
        spec.write_text(_spec(a.base_url), encoding="utf-8")
        rc = platform_main(["--targets", f"{a.base_url},{b.base_url}",
                            "--openapi", str(spec), "--out", str(tmp_path / "plat")])
        assert rc == 0
    finally:
        a.stop()
        b.stop()
    return (tmp_path / "plat" / "platform-report.md").read_text(encoding="utf-8")


def test_reason_column_is_a_sentence_not_a_bare_code(platform_report: str) -> None:
    rows = [ln for ln in platform_report.splitlines()
            if ln.startswith("| hos_ares/") and ln.count("|") >= 5]
    assert rows, platform_report[:400]
    bare = [ln for ln in rows if " · `" not in ln]
    assert not bare, f"这些行的原因还是裸码：{bare[:3]}"

def test_reason_column_keeps_the_code_for_traceability(platform_report: str) -> None:
    """人话在前、码在后——对照 findings.json 时要用码。"""
    line = next(ln for ln in platform_report.splitlines()
                if "symbol_missing:SAFE_FILE" in ln)
    assert "缺输入符号" in line and "`symbol_missing:SAFE_FILE`" in line, line


# ── B256：汇总报告也要能证明没被手改 ────────────────────────────
def test_platform_report_has_a_manifest_that_matches(platform_report, tmp_path) -> None:  # noqa: ANN001
    import hashlib

    plat = tmp_path / "plat"
    mf = plat / "platform-report.manifest.json"
    assert mf.exists(), "汇总报告没有清单，被手改无法证明"
    data = json.loads(mf.read_text(encoding="utf-8"))
    body = (plat / "platform-report.md").read_text(encoding="utf-8")
    want = hashlib.sha256(body.encode("utf-8")).hexdigest()
    # 形状与资产级清单对齐（B257）：version + sections + report_sha256
    assert data["version"] == 1
    assert data["sections"] == {"markdown": want}, data["sections"]
    assert data["report_sha256"] == want
    assert data["assets"] >= 1
    # 只覆盖 markdown：不许出现 sarif/json 节
    assert set(data["sections"]) == {"markdown"}, data["sections"]
