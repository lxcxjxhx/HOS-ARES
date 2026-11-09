"""B181：控制台的页面**从来没被浏览器执行过**（只测过 DOM 契约）。

判据：仓库里 11 个 `test_ui_*.py` 全是静态的——读 `index.html` 字符串、
用 TestClient 打 `/ui/*` 接口。没有一个用例真的把页面跑起来。于是
"JS 里有个语法错误"或"取数路径写错"这类问题，全部不会被发现。

做法：起一个真的 uvicorn，用项目自带的 `BrowserRenderer` 打开页面并切页，
断言渲染后的 DOM 里出现**载荷里的真实数据**——只有 JS 真的跑通、真的取到数，
那些字才会出现。

驱动在**独立解释器**里跑（`_console_browser_driver.py`）：uvicorn 会在自己的
线程里建事件循环，而 Playwright 的同步 API 不许在"当前线程已有循环"时使用；
同进程跑时会被别的用例留下的循环绊倒（实测：单跑绿、整跑红）。

浏览器不可用就 skip（与 `test_browser_render.py` 同口径）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests", ROOT / "tests" / "unit"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

SEP = chr(0x2F)
UI_OVERVIEW = SEP + "ui" + SEP + "overview"
UI_FINDINGS = SEP + "ui" + SEP + "findings"
DRIVER = Path(__file__).with_name("_console_browser_driver.py")


def _available() -> bool:
    from veripen_core.render import BrowserRenderer

    return BrowserRenderer().available()


pytestmark = pytest.mark.skipif(not _available(), reason="浏览器不可用：页面未执行")


def _record(tmp_path: Path) -> Path:
    """最小 run 产物：一条已确认的发现（资产名刻意取得好认）。"""
    import capsule_fixture as F

    from veripen_core.evidence import seal_capsule_doc

    cap = seal_capsule_doc(F.capsule())
    doc: dict[str, Any] = {
        "schema": "veripen.report/v1",
        "engagement": "b181-console",
        "root_sha256": cap.chain.self_sha256,
        "counts": {"total": 1, "by_outcome": {"reported": 1}},
        "findings": [
            {
                "finding_id": cap.finding_id,
                "oracle": cap.oracle.id,
                "asset": "asset-b181",
                "verdict": "VERIFIED",
                "outcome": "reported",
                "severity": "high",
                "title": "浏览器执行探针",
                "route_ref": cap.route.ref if cap.route else "",
                "capsule_id": cap.capsule_id,
            }
        ],
    }
    path = tmp_path / "record.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


def test_console_page_runs_in_a_real_browser(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(DRIVER), str(_record(tmp_path))],
        capture_output=True, text=True, encoding="utf-8", timeout=300,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    out = proc.stdout or ""
    assert proc.returncode == 0, (out + (proc.stderr or ""))[-1500:]
    assert "SKIP" not in out.splitlines()[0], "驱动认为浏览器不可用（与用例的前置判断不一致）"

    head = out.splitlines()
    assert head[0].startswith("CHANNEL ") and head[0].split(" ", 1)[1].strip(), head[:3]
    assert head[1] == "OUTCOME ok ok", head[:3]
    # 页面自己去取数了——这条路径只有 JS 跑起来才会出现
    assert UI_OVERVIEW in head[2], head[2]
    assert UI_FINDINGS in head[3], head[3]

    # 切到发现页：载荷里的资产名必须真的进到 DOM（表格由 JS 渲染）
    assert "asset-b181" in out, out[-1200:]
    assert "<td>undefined</td>" not in out, "表格里出现 undefined：取数没取到"


# ── 平台（多资产）页面也在真浏览器里跑一遍（B182）───────────────
def _asset_dir(root: Path, name: str, oracle: str, *, outcome: str = "reported") -> None:
    sub = root / name
    sub.mkdir(parents=True, exist_ok=True)
    doc = {
        "schema": "veripen.report/v1",
        "engagement": name,
        "root_sha256": "0" * 64,
        "counts": {"total": 1, "by_outcome": {"reported": 1}},
        "findings": [
            {
                "finding_id": f"{name}-F1",
                "oracle": oracle,
                "asset": name,
                "verdict": "VERIFIED",
                "outcome": outcome,
                "severity": "high",
                "title": f"{name} 的发现",
                "route_ref": "route-" + name,
                "route_strength": "strong",
                "confidence": 0.95,
                "reason_code": "" if outcome == "reported" else "missing_input:auth:secondary",
                "capsule_id": "cap-" + name,
            }
        ],
    }
    (sub / "findings.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    ledger = {
        "schema_version": "1",
        "asset_id": name,
        "summary": {"faces": 2, "faces_covered": 2, "oracles_selected": 1,
                    "applicable_pairs": 2, "executed": 2, "gaps": 1,
                    "outcomes": {"reported": 1}},
        "completeness": {"complete": True, "caveats": []},
        "gaps": [{"kind": "oracle_blocked", "severity": "warn", "oracle_id": oracle}],
    }
    (sub / "coverage.json").write_text(json.dumps(ledger), encoding="utf-8")


def test_platform_console_renders_the_merged_ledger(tmp_path: Path) -> None:
    """多资产合计在页面上必须自报口径（"平台合计账本"），而不是某个资产的面数。"""
    root = tmp_path / "pf"
    _asset_dir(root, "asset-1", "csp_absent")
    _asset_dir(root, "asset-2", "xss_dom")

    proc = subprocess.run(
        [sys.executable, str(DRIVER), str(root), "coverage", "findings?asset=asset-2"],
        capture_output=True, text=True, encoding="utf-8", timeout=300,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    out = proc.stdout or ""
    assert proc.returncode == 0, (out + (proc.stderr or ""))[-1500:]
    assert "===== page coverage =====" in out, out[:400]

    page = out.split("===== page coverage =====", 1)[1].split("----- overview -----", 1)[0]
    assert "平台合计账本" in page, page[-1200:]
    assert "asset-1" in page and "asset-2" in page, "两个资产都得出现在合计里"


def test_findings_page_filters_by_asset(tmp_path: Path) -> None:
    """多资产时页面上该有筛选器；选中 asset-2 后，asset-1 的行不该还在表里（B183）。"""
    root = tmp_path / "pf"
    _asset_dir(root, "asset-1", "csp_absent")
    _asset_dir(root, "asset-2", "xss_dom")

    proc = subprocess.run(
        [sys.executable, str(DRIVER), str(root), "findings?asset=asset-2"],
        capture_output=True, text=True, encoding="utf-8", timeout=300,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    out = proc.stdout or ""
    assert proc.returncode == 0, (out + (proc.stderr or ""))[-1500:]

    marker = "===== page findings?asset=asset-2 ====="
    assert marker in out, out[:400]
    page = out.split(marker, 1)[1].split("----- overview -----", 1)[0]
    assert 'id="asset"' in page, "多资产时该画出资产筛选器"
    # 平台加载器会把 id 改成 `asset-N/F-00k`（撞 id 就串页，B160）
    assert 'data-id="asset-2/F-001"' in page, "筛到 asset-2 后它自己的行必须还在"
    assert 'data-id="asset-1/F-001"' not in page, "筛到 asset-2 后别的资产的行不该出现"


def test_unverified_page_scopes_counts_to_the_asset(tmp_path: Path) -> None:
    """筛到某个资产后，"未验证几条"必须按本资产说，并同时给出全局数（B184）。"""
    root = tmp_path / "pf"
    _asset_dir(root, "asset-1", "csp_absent", outcome="undetermined")
    _asset_dir(root, "asset-2", "xss_dom", outcome="undetermined")

    proc = subprocess.run(
        [sys.executable, str(DRIVER), str(root), "unverified?asset=asset-1"],
        capture_output=True, text=True, encoding="utf-8", timeout=300,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    out = proc.stdout or ""
    assert proc.returncode == 0, (out + (proc.stderr or ""))[-1500:]

    marker = "===== page unverified?asset=asset-1 ====="
    assert marker in out, out[:400]
    page = out.split(marker, 1)[1].split("----- overview -----", 1)[0]
    assert 'id="asset"' in page, "多资产时该画出资产筛选器"
    assert "本资产 1 条 / 全局 2 条" in page, page[-1500:]
    assert "asset-1/F-001" in page, "筛到 asset-1 后它自己的未验证行必须还在"
    assert "asset-2/F-001" not in page, "筛到 asset-1 后别的资产的行不该出现"
